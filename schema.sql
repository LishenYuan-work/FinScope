create extension if not exists pgcrypto;
create extension if not exists vector;

create table if not exists public.analysis_tasks (
  id uuid primary key default gen_random_uuid(),
  user_id uuid references auth.users(id) on delete cascade,
  filename text not null,
  storage_path text not null,
  status text not null check (status in ('pending','parsing','finance_analyzing','risk_analyzing','summary_finish','error')),
  error_message text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.analysis_reports (
  task_id uuid primary key references public.analysis_tasks(id) on delete cascade,
  markdown text not null,
  report_json jsonb not null,
  created_at timestamptz not null default now()
);

create index if not exists analysis_tasks_created_at_idx on public.analysis_tasks (created_at desc);
create index if not exists analysis_tasks_status_idx on public.analysis_tasks (status);

-- Existing installations created before account login can migrate safely.
alter table public.analysis_tasks
  add column if not exists user_id uuid references auth.users(id) on delete cascade;
create index if not exists analysis_tasks_user_id_idx on public.analysis_tasks (user_id, created_at desc);

-- Durable SSE audit trail. Clients can replay rows after a network
-- interruption by sending Last-Event-ID on the next connection.
create table if not exists public.analysis_task_events (
  id bigint generated always as identity primary key,
  task_id uuid not null references public.analysis_tasks(id) on delete cascade,
  event text not null,
  payload jsonb not null,
  created_at timestamptz not null default now()
);

create index if not exists analysis_task_events_task_id_idx
  on public.analysis_task_events(task_id, id);

create table if not exists public.document_chunks (
  id bigint generated always as identity primary key,
  task_id uuid not null references public.analysis_tasks(id) on delete cascade,
  chunk_id text not null,
  page integer not null check (page > 0),
  content text not null,
  embedding vector(1536) not null,
  created_at timestamptz not null default now(),
  unique (task_id, chunk_id)
);

create index if not exists document_chunks_task_idx on public.document_chunks (task_id);
create index if not exists document_chunks_embedding_idx on public.document_chunks using ivfflat (embedding vector_cosine_ops) with (lists = 100);

create or replace function public.match_document_chunks(
  query_embedding vector(1536),
  match_task_id uuid,
  match_count int default 8
)
returns table (chunk_id text, page int, content text, similarity float)
language sql stable as $$
  select c.chunk_id, c.page, c.content,
         1 - (c.embedding <=> query_embedding) as similarity
  from public.document_chunks c
  where c.task_id = match_task_id
  order by c.embedding <=> query_embedding
  limit match_count;
$$;

-- Recommended when using the service key only from the backend.
alter table public.analysis_tasks enable row level security;
alter table public.analysis_reports enable row level security;
alter table public.document_chunks enable row level security;
alter table public.analysis_task_events enable row level security;
