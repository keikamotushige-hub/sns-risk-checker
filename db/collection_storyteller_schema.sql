-- Collection Storyteller schema (Supabase / Postgres)
-- Run in Supabase SQL Editor when enabling the collector archive product.
-- auth.users is managed by Supabase; profiles extends it.

create extension if not exists "pgcrypto";

create table if not exists public.profiles (
  id uuid primary key references auth.users(id) on delete cascade,
  display_name text,
  avatar_url text,
  collector_bio text,
  locale text not null default 'ja',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.collections (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references public.profiles(id) on delete cascade,
  title text not null,
  theme text,
  cover_media_id uuid,
  sort_order int not null default 0,
  is_private boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

do $$ begin
  create type public.item_status as enum ('draft', 'appraising', 'ready', 'archived');
exception when duplicate_object then null;
end $$;

create table if not exists public.items (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references public.profiles(id) on delete cascade,
  collection_id uuid references public.collections(id) on delete set null,
  title text,
  brand text,
  maker text,
  category text,
  era_label text,
  acquired_at date,
  acquired_from text,
  condition_note text,
  status public.item_status not null default 'draft',
  primary_media_id uuid,
  attachment_score smallint check (attachment_score between 0 and 100),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

do $$ begin
  create type public.media_kind as enum ('photo', 'detail', 'document', 'video');
exception when duplicate_object then null;
end $$;

create table if not exists public.item_media (
  id uuid primary key default gen_random_uuid(),
  item_id uuid not null references public.items(id) on delete cascade,
  owner_id uuid not null references public.profiles(id) on delete cascade,
  kind public.media_kind not null default 'photo',
  storage_path text not null,
  width int,
  height int,
  caption text,
  sort_order int not null default 0,
  created_at timestamptz not null default now()
);

do $$ begin
  create type public.job_status as enum ('queued', 'running', 'succeeded', 'failed', 'cancelled');
exception when duplicate_object then null;
end $$;

create table if not exists public.appraisal_jobs (
  id uuid primary key default gen_random_uuid(),
  item_id uuid not null references public.items(id) on delete cascade,
  owner_id uuid not null references public.profiles(id) on delete cascade,
  status public.job_status not null default 'queued',
  model text,
  error_message text,
  started_at timestamptz,
  finished_at timestamptz,
  created_at timestamptz not null default now()
);

create table if not exists public.appraisals (
  id uuid primary key default gen_random_uuid(),
  item_id uuid not null references public.items(id) on delete cascade,
  owner_id uuid not null references public.profiles(id) on delete cascade,
  job_id uuid references public.appraisal_jobs(id) on delete set null,
  identified_name text not null,
  confidence numeric(4,3),
  story_markdown text not null,
  craft_highlights jsonb not null default '[]',
  caution_notes jsonb not null default '[]',
  raw_model_json jsonb,
  is_current boolean not null default true,
  created_at timestamptz not null default now()
);

create table if not exists public.journals (
  id uuid primary key default gen_random_uuid(),
  item_id uuid not null references public.items(id) on delete cascade,
  owner_id uuid not null references public.profiles(id) on delete cascade,
  entry_date date not null default (timezone('utc', now()))::date,
  mood text,
  body text not null,
  ai_summary text,
  tags text[] not null default '{}',
  media_ids uuid[] not null default '{}',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.valuations (
  id uuid primary key default gen_random_uuid(),
  item_id uuid not null references public.items(id) on delete cascade,
  owner_id uuid not null references public.profiles(id) on delete cascade,
  currency text not null default 'JPY',
  low_amount numeric(14,2),
  mid_amount numeric(14,2),
  high_amount numeric(14,2),
  affection_rationale text not null,
  market_rationale text,
  data_quality text not null default 'estimated',
  is_current boolean not null default true,
  created_at timestamptz not null default now()
);

create table if not exists public.market_snapshots (
  id uuid primary key default gen_random_uuid(),
  valuation_id uuid not null references public.valuations(id) on delete cascade,
  source_label text not null,
  source_url text,
  observed_amount numeric(14,2),
  observed_at date,
  note text,
  created_at timestamptz not null default now()
);

do $$ begin
  create type public.share_tone as enum ('proud', 'poetic', 'concise', 'educational');
exception when duplicate_object then null;
end $$;

create table if not exists public.share_cards (
  id uuid primary key default gen_random_uuid(),
  item_id uuid not null references public.items(id) on delete cascade,
  owner_id uuid not null references public.profiles(id) on delete cascade,
  tone public.share_tone not null default 'proud',
  headline text not null,
  body text not null,
  hashtags text[] not null default '{}',
  created_at timestamptz not null default now()
);
