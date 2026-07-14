-- M13 GitHub Sourcing — full schema
-- Run once in the Supabase SQL editor. All statements are idempotent.
-- ============================================================

-- 1. Normalized candidate profiles (global, shared across users)
create table if not exists candidates (
    id bigint generated always as identity primary key,
    handle text unique not null,
    name text,
    location text,
    bio text,
    company text,
    followers int,
    public_repos int,
    top_repos text,
    contributes_to_ai boolean,
    signal_score int,
    founder_badges text,
    account_age_years float,
    github_url text,
    first_seen timestamptz default now(),
    last_seen timestamptz default now(),
    times_seen int default 1,
    is_new boolean default true
);

-- 2. App users (created on first login via Google OAuth)
create table if not exists users (
    id bigint generated always as identity primary key,
    email text unique not null,
    name text,
    created_at timestamptz default now(),
    last_login_at timestamptz default now()
);

-- 3. Saved search configurations (per user)
create table if not exists saved_searches (
    id bigint generated always as identity primary key,
    search_id text unique not null,
    user_email text not null references users(email) on delete cascade,
    name text not null,
    intent text not null,
    mode text not null,
    filters jsonb default '{}',
    notify_on_new boolean default true,
    created_at timestamptz default now(),
    last_run_at timestamptz,
    last_result_count int default 0
);

-- 4. Per-run records (who ran what, when, how many results)
create table if not exists search_runs (
    id bigint generated always as identity primary key,
    run_id text unique not null,
    user_email text not null,
    saved_search_id text references saved_searches(search_id) on delete set null,
    intent text not null,
    mode text not null,
    filters jsonb default '{}',
    ran_at timestamptz default now(),
    result_count int default 0,
    triggered_by text default 'manual'  -- 'manual' | 'scheduler'
);

-- 5. Per-run match tracking with explainability
create table if not exists candidate_matches (
    id bigint generated always as identity primary key,
    run_id text not null references search_runs(run_id) on delete cascade,
    handle text not null,
    signal_score int,
    match_reasons jsonb default '[]',
    first_matched_at timestamptz default now(),
    last_matched_at timestamptz default now(),
    notified_at timestamptz,
    source_query text,
    unique(run_id, handle)
);

-- 6. Notification preferences (per user)
create table if not exists notification_preferences (
    id bigint generated always as identity primary key,
    user_email text unique not null references users(email) on delete cascade,
    notify_email text not null,
    recap_frequency text default 'Weekly',
    notify_on_new_match boolean default true,
    digest_recipients text[] default '{}',
    updated_at timestamptz default now()
);

-- 7. Audit log
create table if not exists audit_log (
    id bigint generated always as identity primary key,
    user_email text,
    action text not null,        -- 'search_run', 'notification_sent', 'saved_search_created', etc.
    detail jsonb default '{}',
    created_at timestamptz default now()
);

-- 8. Point-in-time snapshots for velocity tracking
create table if not exists candidate_snapshots (
    id bigint generated always as identity primary key,
    handle text not null,
    recorded_at timestamptz default now(),
    followers int,
    public_repos int,
    signal_score int,
    top_repo_stars int  -- total stars across top repos at snapshot time
);
create index if not exists idx_snapshots_handle on candidate_snapshots(handle, recorded_at desc);

-- ── Indexes ──────────────────────────────────────────────────────────────────
create index if not exists idx_saved_searches_user on saved_searches(user_email);
create index if not exists idx_search_runs_user on search_runs(user_email, ran_at desc);
create index if not exists idx_search_runs_saved on search_runs(saved_search_id);
create index if not exists idx_candidate_matches_run on candidate_matches(run_id);
create index if not exists idx_candidate_matches_handle on candidate_matches(handle);
create index if not exists idx_candidates_score on candidates(signal_score desc);
create index if not exists idx_audit_log_user on audit_log(user_email, created_at desc);

-- ── User context helper ───────────────────────────────────────────────────────
-- The Streamlit app calls this RPC with the authenticated user's email before
-- any user-scoped query. RLS policies read it back via current_setting().
-- The scheduler uses the service key which bypasses RLS entirely.

create or replace function set_user_context(email text)
returns void
language sql
security definer
as $$
  select set_config('app.current_user_email', email, true);
$$;

-- Helper used in RLS policies
create or replace function current_user_email()
returns text
language sql
stable
as $$
  select nullif(current_setting('app.current_user_email', true), '');
$$;

-- ── Row-Level Security ───────────────────────────────────────────────────────
-- The Streamlit app uses the anon key → subject to RLS.
-- The scheduler uses the service key → bypasses RLS (trusted server process).

alter table saved_searches enable row level security;
alter table search_runs enable row level security;
alter table candidate_matches enable row level security;
alter table notification_preferences enable row level security;
alter table audit_log enable row level security;

-- Drop old catch-all policies before creating scoped ones
drop policy if exists "service_all_saved_searches" on saved_searches;
drop policy if exists "service_all_search_runs" on search_runs;
drop policy if exists "service_all_candidate_matches" on candidate_matches;
drop policy if exists "service_all_notification_prefs" on notification_preferences;
drop policy if exists "service_all_audit_log" on audit_log;

-- saved_searches: users see and modify only their own rows
create policy "own_saved_searches"
    on saved_searches for all
    using (user_email = current_user_email())
    with check (user_email = current_user_email());

-- search_runs: users see only their own runs
create policy "own_search_runs"
    on search_runs for all
    using (user_email = current_user_email())
    with check (user_email = current_user_email());

-- candidate_matches: scoped through run_id → only runs owned by the user
create policy "own_candidate_matches"
    on candidate_matches for all
    using (
        run_id in (
            select run_id from search_runs where user_email = current_user_email()
        )
    );

-- notification_preferences: one row per user, strictly scoped
create policy "own_notification_preferences"
    on notification_preferences for all
    using (user_email = current_user_email())
    with check (user_email = current_user_email());

-- audit_log: users see their own entries; scheduler writes with service key
create policy "own_audit_log"
    on audit_log for select
    using (user_email = current_user_email());

create policy "insert_audit_log"
    on audit_log for insert
    with check (true);  -- any authenticated session may write; reads are scoped above

-- candidates table: global shared read, no RLS needed (not user-scoped)
-- (intentionally left without RLS — it's a shared profile store)
