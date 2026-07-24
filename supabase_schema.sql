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

-- 9. Per-user profile status and notes (pipeline tracking)
create table if not exists candidate_actions (
    id bigint generated always as identity primary key,
    user_email text not null,
    handle text not null,
    status text not null default 'none',  -- 'none' | 'interested' | 'contacted' | 'passed'
    note text default '',
    notify_frequency text not null default 'daily'
        check (notify_frequency in ('daily', 'weekly', 'off')),
    updated_at timestamptz default now(),
    unique(user_email, handle)
);
create index if not exists idx_candidate_actions_user on candidate_actions(user_email, status);
alter table candidate_actions enable row level security;
alter table candidate_actions
    add column if not exists notify_frequency text not null default 'daily';
alter table candidate_actions
    drop constraint if exists candidate_actions_notify_frequency_check;
alter table candidate_actions
    add constraint candidate_actions_notify_frequency_check
    check (notify_frequency in ('daily', 'weekly', 'off'));

-- ── Indexes ──────────────────────────────────────────────────────────────────
create index if not exists idx_saved_searches_user on saved_searches(user_email);
create index if not exists idx_search_runs_user on search_runs(user_email, ran_at desc);
create index if not exists idx_search_runs_saved on search_runs(saved_search_id);
create index if not exists idx_candidate_matches_run on candidate_matches(run_id);
create index if not exists idx_candidate_matches_handle on candidate_matches(handle);
create index if not exists idx_candidates_score on candidates(signal_score desc);
create index if not exists idx_audit_log_user on audit_log(user_email, created_at desc);

-- ── Database boundary ────────────────────────────────────────────────────────
-- Google OAuth is verified by the Streamlit server, not Supabase Auth. Because
-- Supabase cannot validate a Google ID token as its own auth.jwt(), direct REST
-- table access is denied to anon/authenticated roles. All database access goes
-- through database.py on the trusted server, which applies verified-email owner
-- filters to every user-scoped query. The service key never reaches the browser.

alter table users enable row level security;
alter table candidates enable row level security;
alter table saved_searches enable row level security;
alter table search_runs enable row level security;
alter table candidate_matches enable row level security;
alter table notification_preferences enable row level security;
alter table audit_log enable row level security;
alter table candidate_snapshots enable row level security;

-- Drop old catch-all policies before creating scoped ones
drop policy if exists "service_all_saved_searches" on saved_searches;
drop policy if exists "service_all_search_runs" on search_runs;
drop policy if exists "service_all_candidate_matches" on candidate_matches;
drop policy if exists "service_all_notification_prefs" on notification_preferences;
drop policy if exists "service_all_audit_log" on audit_log;
drop policy if exists "own_saved_searches" on saved_searches;
drop policy if exists "own_search_runs" on search_runs;
drop policy if exists "own_candidate_matches" on candidate_matches;
drop policy if exists "own_notification_preferences" on notification_preferences;
drop policy if exists "own_audit_log" on audit_log;
drop policy if exists "insert_audit_log" on audit_log;

revoke all on table users, candidates, saved_searches, search_runs,
    candidate_matches, notification_preferences, audit_log, candidate_snapshots
    from anon, authenticated;

-- With RLS enabled and no anon/authenticated policies, direct client access
-- returns no rows even if a public project key is discovered. service_role
-- bypasses RLS and is used only inside the trusted server process.
