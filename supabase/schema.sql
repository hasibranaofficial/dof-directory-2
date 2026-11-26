-- Supabase > SQL Editor-এ পুরোটা পেস্ট করে Run করুন
create table offices (
  id bigint generated always as identity primary key,
  name text not null, type text not null,
  division text, district text, upazila text,
  base_url text unique not null,
  active boolean default true, last_ok timestamptz, last_error text
);
create table officers (
  id bigint generated always as identity primary key,
  office_id bigint references offices(id) on delete cascade,
  key text not null, name text not null, designation text,
  phone text, email text,
  photo_url text, photo_path text,
  active boolean default true, locked boolean default false,
  missed_runs int default 0, updated_at timestamptz default now(),
  unique (office_id, key)
);
create table change_log (
  id bigint generated always as identity primary key,
  at timestamptz default now(), office_id bigint, officer_key text,
  action text, detail jsonb
);

-- অ্যাডমিন (Table Editor) যা বদলাবে তা স্বয়ংক্রিয়ভাবে locked হবে; স্ক্র্যাপার সেটা আর বদলাবে না
create function lock_manual_edit() returns trigger language plpgsql as $$
begin
  new.updated_at := now();
  if current_user <> 'service_role' then new.locked := true; end if;
  return new;
end $$;
create trigger officers_lock before insert or update on officers
  for each row execute function lock_manual_edit();

alter table offices enable row level security;
alter table officers enable row level security;
alter table change_log enable row level security;
create policy "public read offices" on offices for select using (active);
create policy "public read officers" on officers for select using (active);

insert into storage.buckets (id, name, public) values ('photos','photos',true)
  on conflict do nothing;
