-- Supabase SQL Editor で実行してください

create table if not exists public.risk_checks (
  id uuid primary key default gen_random_uuid(),
  user_id text,
  input_text text not null,
  result text not null,
  created_at timestamptz not null default now()
);

alter table public.risk_checks
  add column if not exists user_id text;

create table if not exists public.user_daily_usage (
  user_id text not null,
  usage_date date not null,
  use_count integer not null default 0,
  primary key (user_id, usage_date)
);

alter table public.risk_checks enable row level security;
alter table public.user_daily_usage enable row level security;

drop policy if exists "Allow insert for authenticated and anon" on public.risk_checks;
create policy "Allow insert for authenticated and anon"
  on public.risk_checks
  for insert
  to anon, authenticated
  with check (true);

drop policy if exists "Allow select for authenticated" on public.risk_checks;
create policy "Allow select for authenticated"
  on public.risk_checks
  for select
  to authenticated
  using (true);

drop policy if exists "Allow usage upsert for anon service" on public.user_daily_usage;
create policy "Allow usage upsert for anon service"
  on public.user_daily_usage
  for all
  to anon, authenticated
  using (true)
  with check (true);
