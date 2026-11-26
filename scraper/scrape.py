"""প্রতিদিন সব অফিসের কর্মকর্তা-কর্মচারীর তালিকা সংগ্রহ ও আপডেট (প্রতিজনের 'দেখুন' পেজসহ)।"""
import os, re, csv, hashlib, datetime, requests, urllib3
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin
from bs4 import BeautifulSoup

urllib3.disable_warnings()
URL = os.environ["SUPABASE_URL"].rstrip("/")
KEY = os.environ["SUPABASE_SERVICE_KEY"]
PATH = os.environ.get("OFFICERS_PATH", "/pages/officers?page=1&page_size=250")
FULL_PHOTOS = os.environ.get("FULL_PHOTOS") == "1"
H = {"apikey": KEY}
if KEY.startswith("eyJ"): H["Authorization"] = f"Bearer {KEY}"
UA = {"User-Agent": "DoF-Directory-Bot/1.0 (official staff directory)"}
PHONE = re.compile(r"(?:\+?880|\+৮৮০|0|০)[\d০-৯\-\s]{8,}")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
FIELDS = ("name", "name_en", "designation", "phone", "email", "photo_url",
          "join_date", "cadre_id", "bcs_batch", "extra")
CARD = re.compile(r"officer|member|staff|profile|card", re.I)
NOW = datetime.datetime.now(datetime.timezone.utc)
WEEK_AGO = (NOW - datetime.timedelta(days=7)).isoformat()[:19]
DEBUG_LEFT = [3]   # প্রথম ৩টি 'দেখুন' পেজের লেবেল লগে ছাপা হবে

def get(url, **kw):
    """সরকারি সাইটের অসম্পূর্ণ SSL সনদ থাকলে যাচাই ছাড়া আবার চেষ্টা করে।"""
    try:
        return requests.get(url, headers=UA, **kw)
    except requests.exceptions.SSLError:
        return requests.get(url, headers=UA, verify=False, **kw)

def api(method, path, **kw):
    h = {**H, "Content-Type": "application/json", **kw.pop("headers", {})}
    r = requests.request(method, f"{URL}/rest/v1/{path}", headers=h, timeout=60, **kw)
    if not r.ok: print("API ERROR", r.status_code, r.text[:300])
    r.raise_for_status()
    return r.json() if r.text else None

def clean(t): return re.sub(r"\s+", " ", t or "").strip()

def label_of(tr):
    c = tr.find(["th", "td"])
    return clean(c.get_text(" ")).rstrip(":：").strip() if c else ""

def field_of(l):
    """পেজের লেবেল থেকে ডেটাবেসের ঘর ঠিক করে; অচেনা লেবেল 'x:' দিয়ে শুরু হয়ে extra-তে যায়।"""
    low = l.lower()
    if "ইংরেজি" in l or low.startswith("name"): return "name_en"
    if l == "নাম" or l.startswith("নাম (বাংলা") or l.startswith("নাম(বাংলা"): return "name"
    if "পদবি" in l or "পদবী" in l: return "designation"
    if "ইমেইল" in l or "ই-মেইল" in l or "ই মেইল" in l or "email" in low: return "email"
    if "মোবাইল" in l: return "phone"
    if "যোগদান" in l: return "join_date"
    if "ক্যাডার" in l or "cadre" in low: return "cadre_id"
    if "বিসিএস" in l or "bcs" in low or "ব্যাচ" in l: return "bcs_batch"
    if "কক্ষ" in l: return "room"
    if "ওয়েব" in l: return "web"
    if "ইন্টারকম" in l: return "intercom"
    if "ফ্যাক্স" in l: return "fax"
    if "ফোন" in l: return "office_phone"
    if l == "অফিস": return "office"
    return "x:" + l

def blank(name, photo):
    return {"name": name, "name_en": None, "designation": None, "phone": None, "email": None,
            "photo_url": photo, "join_date": None, "cadre_id": None, "bcs_batch": None,
            "extra": {}, "_url": None}

def parse_labeled(s, base):
    """"লেবেল | মান" সারির কাঠামো: 'নাম' সারি এলে নতুন কর্মকর্তা শুরু হয়; 'দেখুন' লিংকও ধরা হয়।"""
    out, cur, last_img = [], None, None
    def src(img):
        return urljoin(base + "/", img["src"]) if img is not None and img.get("src") else None
    for el in s.find_all(["img", "tr", "a"]):
        if el.name == "img":
            last_img = el; continue
        if el.name == "a":
            href = el.get("href") or ""
            if cur is not None and not cur["_url"] and not href.startswith(("mailto:", "tel:", "#")) \
               and (re.search(r"/officers/[^?#/]+", href) or "দেখুন" in el.get_text()):
                cur["_url"] = urljoin(base + "/", href)
            continue
        cells = el.find_all(["th", "td"], recursive=False)
        if len(cells) < 2: continue
        label = label_of(el)
        if not label: continue
        f = field_of(label)
        val = clean(cells[1].get_text(" "))
        if f == "name":
            own = el.find("img")
            tbl = el.find_parent("table")
            if own is None and tbl is not None:
                if sum(1 for t in tbl.find_all("tr") if label_of(t) == "নাম") <= 1:
                    own = tbl.find("img")
                if own is None and tbl.parent is not None and len(tbl.parent.find_all("table")) == 1:
                    own = tbl.parent.find("img")
            cur = blank(val, src(own if own is not None else last_img))
            out.append(cur); last_img = None
        elif cur is not None and val:
            if f == "email": cur["email"] = val.replace(" ", "")
            elif f in ("designation", "phone", "name_en", "join_date", "cadre_id", "bcs_batch"): cur[f] = val
            elif f == "office": pass
            elif f.startswith("x:"):
                if len(label) <= 40: cur["extra"][f[2:]] = val
            else: cur["extra"][f] = val
    for o in out:
        o["extra"] = o["extra"] or None
    return [o for o in out if o["name"]]

def parse(html, base):
    s = BeautifulSoup(html, "html.parser")
    out = parse_labeled(s, base)
    if out: return out
    blocks = []
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
        rest = [c for c in cells if not EMAIL.search(c) and not PHONE.fullmatch(c)
                and not re.fullmatch(r"[\d০-৯\.]+", c)]
        if not rest: continue
        img = el.find("img")
        o = blank(rest[0], urljoin(base + "/", img["src"]) if img and img.get("src") else None)
        o["designation"] = rest[1] if len(rest) > 1 else None
        o["phone"] = clean(pm.group()) if pm else None
        o["email"] = em.group() if em else None
        o["extra"] = None
        a = el.find("a", href=re.compile(r"/officers/[^?#/]+"))
        if a: o["_url"] = urljoin(base + "/", a["href"])
        out.append(o)
    return out

def detail_pairs(s):
    """'দেখুন' পেজ থেকে (লেবেল, মান) জোড়া: টেবিল, dt/dd, দুই-অংশের ব্লক ও 'লেবেল: মান' লাইন।"""
    pairs = []
    for tr in s.find_all("tr"):
        c = tr.find_all(["th", "td"], recursive=False)
        if len(c) >= 2: pairs.append((clean(c[0].get_text(" ")).rstrip(":：").strip(), clean(c[1].get_text(" "))))
    for dt in s.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        if dd: pairs.append((clean(dt.get_text(" ")).rstrip(":：").strip(), clean(dd.get_text(" "))))
    for el in s.find_all(["div", "li", "p", "section"]):
        kids = [k for k in el.find_all(True, recursive=False)]
        if len(kids) == 2 and not any(k.find(True) and len(k.find_all(True)) > 3 for k in kids):
            a, b = clean(kids[0].get_text(" ")).rstrip(":：").strip(), clean(kids[1].get_text(" "))
            if a and b and len(a) <= 40 and not field_of(a).startswith("x:"): pairs.append((a, b))
    for line in s.get_text("\n").split("\n"):
        line = clean(line)
        for sep in (":", "："):
            if sep in line:
                a, b = [x.strip() for x in line.split(sep, 1)]
                if a and b and len(a) <= 40 and not field_of(a).startswith("x:"): pairs.append((a, b))
                break
    return pairs

def parse_detail(html):
    s = BeautifulSoup(html, "html.parser")
    for t in s(["script", "style", "noscript"]): t.decompose()
    res = {"extra": {}}
    for label, val in detail_pairs(s):
        if not label or not val: continue
        f = field_of(label)
        if f in ("name", "designation", "phone", "name_en", "join_date", "cadre_id", "bcs_batch"):
            res.setdefault(f, val)
        elif f == "email": res.setdefault("email", val.replace(" ", ""))
        elif f == "office": pass
        elif f.startswith("x:"): pass   # অচেনা লেবেল (যেমন জন্মতারিখ, ঠিকানা) নিরাপত্তার জন্য সংরক্ষণ করা হয় না
        else: res["extra"].setdefault(f, val)
    return res

def fill_detail(row):
    """প্রতিজনের 'দেখুন' পেজ থেকে ক্যাডার আইডি, বিসিএস ব্যাচ, যোগদানের তারিখ ইত্যাদি আনে।"""
    try:
        r = get(row["_url"], timeout=45); r.raise_for_status()
        d = parse_detail(r.text)
        if DEBUG_LEFT[0] > 0:
            DEBUG_LEFT[0] -= 1
            print("DETAIL নমুনা:", row["name"], "->", {k: v for k, v in d.items() if k != "extra"}, "extra:", d["extra"])
        if d.get("name") and clean(d["name"]) != clean(row["name"]):
            print("সতর্কতা: নাম মেলেনি", row["name"], "|", d["name"]); return
        for f in ("cadre_id", "bcs_batch", "join_date"):
            if d.get(f): row[f] = d[f]
        for f in ("designation", "phone", "email", "name_en"):
            if not row[f] and d.get(f): row[f] = d[f]
        if d["extra"]: row["extra"] = {**(row["extra"] or {}), **d["extra"]}
        row["detail_at"] = NOW.isoformat()
    except Exception as e:
        print("দেখুন পেজ ব্যর্থ", row.get("_url"), str(e)[:100])

def key_of(name, desig): return hashlib.sha1(clean(f"{name}|{desig or ''}").encode()).hexdigest()[:16]

def upload_photo(office_id, key, url):
    try:
        r = get(url, timeout=40); r.raise_for_status()
        ctype = r.headers.get("content-type", "image/jpeg")
        path = f"{office_id}/{key}.{'png' if 'png' in ctype else 'jpg'}"
        u = requests.post(f"{URL}/storage/v1/object/photos/{path}", data=r.content, timeout=60,
                          headers={**H, "Content-Type": ctype, "x-upsert": "true"})
        u.raise_for_status(); return path
    except Exception as e:
        print("ছবি ব্যর্থ", url, e); return None

def run_office(o):
    try:
        r = get(o["base_url"].rstrip("/") + PATH, timeout=45)
        r.raise_for_status()
        rows = parse(r.text, o["base_url"].rstrip("/"))
        if not rows: raise ValueError("কোনো কর্মকর্তা পাওয়া যায়নি (পেজ কাঠামো আলাদা হতে পারে)")
        old = {x["key"]: x for x in api("GET", f"officers?office_id=eq.{o['id']}&select=*&limit=2000")}
        todo = []
        for row in rows:
            row["_k"] = key_of(row["name"], row["designation"]); ex = old.get(row["_k"]); row["_ex"] = ex
            row["detail_at"] = ex.get("detail_at") if ex else None
            if ex:   # বিস্তারিত না আনলে আগের মান ধরে রাখা
                for f in ("cadre_id", "bcs_batch", "join_date"): row[f] = ex.get(f)
                row["extra"] = {**(ex.get("extra") or {}), **(row["extra"] or {})} or None
            stale = (not ex) or (not ex.get("detail_at")) or ex["detail_at"][:19] < WEEK_AGO
            if row["_url"] and not (ex and ex["locked"]) and stale: todo.append(row)
        if todo:
            with ThreadPoolExecutor(3) as tp: list(tp.map(fill_detail, todo))
        seen, upserts, log = set(), [], []
        for row in rows:
            k, ex = row["_k"], row["_ex"]; seen.add(k)
            if ex and ex["locked"]: continue
            changed = (not ex) or (not ex["active"]) or any(ex.get(f) != row[f] for f in FIELDS)
            fetched = row in todo and row["detail_at"] != (ex or {}).get("detail_at")
            if not changed and not fetched and not (FULL_PHOTOS and row["photo_url"]): continue
            photo_path = ex["photo_path"] if ex else None
            if row["photo_url"] and (not ex or ex["photo_url"] != row["photo_url"] or not photo_path or FULL_PHOTOS):
                photo_path = upload_photo(o["id"], k, row["photo_url"]) or photo_path
            upserts.append({**{f: row[f] for f in FIELDS}, "detail_at": row["detail_at"],
                            "office_id": o["id"], "key": k, "photo_path": photo_path,
                            "active": True, "missed_runs": 0})
            if changed:
                log.append({"office_id": o["id"], "officer_key": k, "action": "update" if ex else "add",
                            "detail": {f: row[f] for f in FIELDS}})
        upserts = list({u["key"]: u for u in upserts}.values())
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
