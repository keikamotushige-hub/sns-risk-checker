-- Supabase SQL Editor で実行してください
create table if not exists public.risk_checks (
  id uuid primary key default gen_random_uuid(),
  input_text text not null,
  result text not null,
  created_at timestamptz not null default now()
);

alter table public.risk_checks enable row level security;

-- サーバー（Service Role / anon + insert）からの書き込み用
-- 本番では Service Role キーをサーバー側のみで使うことを推奨
create policy "Allow insert for authenticated and anon"
  on public.risk_checks
  for insert
  to anon, authenticated
  with check (true);

create policy "Allow select for authenticated"
  on public.risk_checks
  for select
  to authenticated
  using (true);
