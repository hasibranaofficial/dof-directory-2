"""প্রতিদিন সব অফিসের কর্মকর্তা-কর্মচারীর তালিকা সংগ্রহ ও আপডেট।"""
import os, re, csv, hashlib, requests
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin
from bs4 import BeautifulSoup

URL = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"]
PATH = os.environ.get("OFFICERS_PATH", "/pages/officers?page=1&page_size=250")
FULL_PHOTOS = os.environ.get("FULL_PHOTOS") == "1"
H = {"apikey": KEY, "Authorization": f"Bearer {KEY}"}
UA = {"User-Agent": "DoF-Directory-Bot/1.0 (official staff directory)"}
PHONE = re.compile(r"(?:\+?880|\+৮৮০|0|০)[\d০-৯\-\s]{8,}")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
FIELDS = ("name", "designation", "phone", "email", "photo_url")
CARD = re.compile(r"officer|member|staff|profile|card", re.I)

def api(method, path, **kw):
    h = {**H, "Content-Type": "application/json", **kw.pop("headers", {})}
    r = requests.request(method, f"{URL}/rest/v1/{path}", headers=h, timeout=60, **kw)
    r.raise_for_status()
    return r.json() if r.text else None

def clean(t): return re.sub(r"\s+", " ", t or "").strip()

def parse(html, base):
    """টেবিল-সারি বা কার্ড-ধরনের তালিকা থেকে কর্মকর্তা বের করে। প্রথম রানের পর সাইট অনুযায়ী ঠিক করতে হতে পারে।"""
    s = BeautifulSoup(html, "html.parser"); out = []; blocks = []
    for tr in s.select("table tr"):
        if len(tr.find_all("td")) >= 2:
            blocks.append((tr, [clean(td.get_text(" ")) for td in tr.find_all("td")]))
    if not blocks:
        for el in s.find_all(True, class_=CARD):
            if el.find("img") and not el.find(True, class_=CARD):
                blocks.append((el, [clean(x) for x in el.get_text("\n").split("\n")]))
    for el, cells in blocks:
        cells = [c for c in cells if c]
        text = " ".join(cells)
        pm, em = PHONE.search(text), EMAIL.search(text)
        phone, email = (clean(pm.group()) if pm else None), (em.group() if em else None)
        rest = [c for c in cells if not EMAIL.search(c) and not PHONE.fullmatch(c)
                and not re.fullmatch(r"[\d০-৯\.]+", c)]
        if not rest: continue
        img = el.find("img")
        out.append({"name": rest[0], "designation": rest[1] if len(rest) > 1 else None,
                    "phone": phone, "email": email,
                    "photo_url": urljoin(base + "/", img["src"]) if img and img.get("src") else None})
    return out

def key_of(name): return hashlib.sha1(clean(name).encode()).hexdigest()[:16]

def upload_photo(office_id, key, url):
    try:
        r = requests.get(url, headers=UA, timeout=40); r.raise_for_status()
        ctype = r.headers.get("content-type", "image/jpeg")
        path = f"{office_id}/{key}.{'png' if 'png' in ctype else 'jpg'}"
        u = requests.post(f"{URL}/storage/v1/object/photos/{path}", data=r.content, timeout=60,
                          headers={**H, "Content-Type": ctype, "x-upsert": "true"})
        u.raise_for_status(); return path
    except Exception as e:
        print("ছবি ব্যর্থ", url, e); return None

def run_office(o):
    try:
        r = requests.get(o["base_url"].rstrip("/") + PATH, headers=UA, timeout=45)
        r.raise_for_status()
        rows = parse(r.text, o["base_url"].rstrip("/"))
        if not rows: raise ValueError("কোনো কর্মকর্তা পাওয়া যায়নি (পেজ কাঠামো আলাদা হতে পারে)")
        old = {x["key"]: x for x in api("GET", f"officers?office_id=eq.{o['id']}&select=*&limit=2000")}
        seen, upserts, log = set(), [], []
        for row in rows:
            k = key_of(row["name"]); seen.add(k); ex = old.get(k)
            if ex and ex["locked"]: continue
            changed = (not ex) or (not ex["active"]) or any(ex.get(f) != row[f] for f in FIELDS)
            if not changed and not (FULL_PHOTOS and row["photo_url"]): continue
            photo_path = ex["photo_path"] if ex else None
            if row["photo_url"] and (not ex or ex["photo_url"] != row["photo_url"] or not photo_path or FULL_PHOTOS):
                photo_path = upload_photo(o["id"], k, row["photo_url"]) or photo_path
            upserts.append({**row, "office_id": o["id"], "key": k, "photo_path": photo_path,
                            "active": True, "missed_runs": 0})
            if changed:
                log.append({"office_id": o["id"], "officer_key": k, "action": "update" if ex else "add",
                            "detail": {f: row[f] for f in FIELDS}})
        if upserts:
            api("POST", "officers?on_conflict=office_id,key", json=upserts,
                headers={"Prefer": "resolution=merge-duplicates"})
        # বাদ পড়া কর্মকর্তা: টানা ৩ দিন না পেলে নিষ্ক্রিয়; অর্ধেকের বেশি একসাথে হারালে কিছুই করা হয় না
        gone = [x for k, x in old.items() if k not in seen and x["active"] and not x["locked"]]
        if len(gone) <= max(2, len(old) // 2):
            for x in gone:
                m = x["missed_runs"] + 1
                api("PATCH", f"officers?id=eq.{x['id']}", json={"missed_runs": m, "active": m < 3})
                if m >= 3: log.append({"office_id": o["id"], "officer_key": x["key"],
                                       "action": "deactivate", "detail": {"name": x["name"]}})
        if log: api("POST", "change_log", json=log)
        api("PATCH", f"offices?id=eq.{o['id']}", json={"last_ok": "now()", "last_error": None})
        return o["name"], True, len(rows)
    except Exception as e:
        try: api("PATCH", f"offices?id=eq.{o['id']}", json={"last_error": str(e)[:300]})
        except Exception: pass
        return o["name"], False, str(e)[:120]

def sync_sites():
    with open(os.path.join(os.path.dirname(__file__), "sites.csv"), encoding="utf-8") as f:
        rows = [{k: (v or None) for k, v in r.items()} for r in csv.DictReader(f)]
    if rows: api("POST", "offices?on_conflict=base_url", json=rows,
                 headers={"Prefer": "resolution=merge-duplicates"})

if __name__ == "__main__":
    sync_sites()
    offices = api("GET", "offices?active=eq.true&select=id,name,base_url&limit=2000")
    with ThreadPoolExecutor(8) as ex: res = list(ex.map(run_office, offices))
    bad = [r for r in res if not r[1]]
    print(f"মোট {len(res)}, সফল {len(res)-len(bad)}, ব্যর্থ {len(bad)}")
    for n, _, e in bad[:50]: print("ব্যর্থ:", n, "-", e)
