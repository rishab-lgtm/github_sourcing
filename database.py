"""
Supabase database module for M13 GitHub Sourcing.
Persists, dedupes, and tracks changes to candidate profiles over time.
"""

import os
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
TABLE = "candidates"


def get_client() -> Client:
    return create_client(SUPABASE_URL, SUPABASE_KEY)


def setup_table():
    """
    Run this once to create the candidates table in Supabase.
    Paste the SQL below into the Supabase SQL editor instead.
    """
    sql = """
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
    """
    print("Run this SQL in your Supabase SQL editor:")
    print(sql)


def upsert_profiles(profiles: list) -> dict:
    """
    Insert new profiles, update existing ones.
    Returns {new: [...], updated: [...]}
    """
    if not profiles:
        return {"new": [], "updated": []}

    client = get_client()
    now = datetime.utcnow().isoformat()

    # Fetch existing handles
    existing = client.table(TABLE).select("handle, times_seen, first_seen").execute()
    existing_map = {r["handle"]: r for r in (existing.data or [])}

    new_profiles = []
    updated_profiles = []

    for p in profiles:
        handle = p.get("handle")
        if not handle:
            continue

        record = {
            "handle": handle,
            "name": p.get("name") or "",
            "location": p.get("location") or "",
            "bio": p.get("bio") or "",
            "company": p.get("company") or "",
            "followers": p.get("followers") or 0,
            "public_repos": p.get("public_repos") or 0,
            "top_repos": p.get("top_repos") or "",
            "contributes_to_ai": p.get("contributes_to_ai") or False,
            "signal_score": p.get("signal_score") or 0,
            "founder_badges": p.get("founder_badges") or "",
            "account_age_years": p.get("account_age_years"),
            "github_url": p.get("github_url") or "",
            "last_seen": now,
        }

        if handle in existing_map:
            record["times_seen"] = existing_map[handle]["times_seen"] + 1
            record["first_seen"] = existing_map[handle]["first_seen"]
            record["is_new"] = False
            updated_profiles.append(record)
        else:
            record["first_seen"] = now
            record["times_seen"] = 1
            record["is_new"] = True
            new_profiles.append(record)

    all_records = new_profiles + updated_profiles
    if all_records:
        client.table(TABLE).upsert(all_records, on_conflict="handle").execute()

    return {"new": new_profiles, "updated": updated_profiles}


def get_all_candidates(limit: int = 500) -> list:
    """Fetch all candidates from Supabase, sorted by signal score."""
    client = get_client()
    result = client.table(TABLE).select("*").order("signal_score", desc=True).limit(limit).execute()
    return result.data or []


def get_new_since(days: int = 7) -> list:
    """Fetch candidates first seen in the last N days."""
    from datetime import timedelta
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    client = get_client()
    result = client.table(TABLE).select("*").gte("first_seen", cutoff).order("signal_score", desc=True).execute()
    return result.data or []
