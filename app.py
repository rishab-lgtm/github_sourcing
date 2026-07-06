"""
M13 GitHub Sourcing — Streamlit UI
"""

import os
import json
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from datetime import datetime, timedelta
from notifications import send_new_profiles_email, send_recap_email, send_weekly_digest
from github_sourcing import find_sf_ai_contributors, find_trending_repo_authors
from database import upsert_profiles, get_all_candidates, get_new_since

DIGEST_RECIPIENTS = ["brent@m13.co", "thomas@m13.co"]

load_dotenv()

SEEN_FILE = "output/seen_profiles.json"
LAST_RECAP_FILE = "output/last_recap.json"
RESULTS_CACHE_FILE = "output/last_results.json"

st.set_page_config(page_title="M13 GitHub Sourcing", page_icon="🔍", layout="wide")

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');
    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
    .stButton > button { border-radius: 6px; font-weight: 500; }
</style>
""", unsafe_allow_html=True)


# --- Persistence helpers ---

def load_seen() -> set:
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    return set()

def save_seen(seen: set):
    os.makedirs("output", exist_ok=True)
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)

def load_last_results() -> list:
    if os.path.exists(RESULTS_CACHE_FILE):
        with open(RESULTS_CACHE_FILE) as f:
            return json.load(f)
    return []

def save_last_results(results: list):
    os.makedirs("output", exist_ok=True)
    with open(RESULTS_CACHE_FILE, "w") as f:
        json.dump(results, f)

def load_last_recap() -> dict:
    if os.path.exists(LAST_RECAP_FILE):
        with open(LAST_RECAP_FILE) as f:
            return json.load(f)
    return {}

def save_last_recap(data: dict):
    os.makedirs("output", exist_ok=True)
    with open(LAST_RECAP_FILE, "w") as f:
        json.dump(data, f)

def score_color(score):
    if score >= 60:
        return "🟢"
    elif score >= 50:
        return "🟡"
    return "🔴"

def recap_due(frequency: str, last_recap: dict) -> bool:
    if not last_recap.get("sent_at"):
        return True
    last = datetime.fromisoformat(last_recap["sent_at"])
    delta = {"Daily": 1, "Bi-Daily": 2, "Weekly": 7, "Monthly": 30}[frequency]
    return datetime.now() >= last + timedelta(days=delta)


# --- Sidebar ---
st.sidebar.title("🔍 GitHub Sourcing")
st.sidebar.markdown("Surface high-signal engineers before they're on anyone's radar.")
st.sidebar.markdown("---")

st.sidebar.markdown("#### Scan Type")
mode = st.sidebar.radio("", ["SF-Based AI Contributors", "Trending Repo Authors"], label_visibility="collapsed")

st.sidebar.markdown("---")
st.sidebar.markdown("#### Filters")

min_score = st.sidebar.slider("Min signal score", 0, 100, 40, step=5)

filter_badges = st.sidebar.multiselect(
    "Founder signals",
    ["🏛 Top Lab", "🚀 Building", "🔬 Researcher"],
    default=[],
    help="Only show profiles with these badges. Leave blank to show all."
)

min_stars = st.sidebar.number_input("Min repo stars", min_value=0, value=0, step=50)

bio_keyword = st.sidebar.text_input("Bio keyword", placeholder="e.g. stealth, agent, infra")

region = st.sidebar.text_input("Region", placeholder="e.g. SF, NYC, London — blank for all")

max_account_age_years = st.sidebar.slider("Max account age (years)", 1, 15, 15, help="Lower = newer accounts. Set to 3 to find people who joined recently and are building fast.")

stealth_only = st.sidebar.checkbox("Stealth only", value=False, help="Only show people who mention 'stealth' in their bio")

sort_by = st.sidebar.selectbox("Sort by", ["Signal Score", "Followers", "Repo Stars"])

st.sidebar.markdown("---")
st.sidebar.markdown("#### Notifications")

notify_email = st.sidebar.text_input("Your email", value=st.session_state.get("saved_email", "rishab@m13.co"))
recap_frequency = st.sidebar.selectbox("Recap frequency", ["Weekly", "Bi-Daily", "Daily", "Monthly"])

if st.sidebar.button("Save"):
    st.session_state["saved_email"] = notify_email
    st.session_state["saved_frequency"] = recap_frequency
    st.sidebar.success("✅ Saved!")

track_mode = st.sidebar.checkbox("Only show new profiles", value=False)

st.sidebar.markdown("---")
st.sidebar.caption("Built for M13 · Powered by GitHub API")


# --- Main ---
st.title("🔍 M13 GitHub Sourcing")
st.markdown("Find talented engineers and future founders before they're on AngelList or TechCrunch.")
st.markdown("---")

col1, col2, col3 = st.columns([3, 1, 1])
with col1:
    run_scan = st.button("Run Scan", type="primary", use_container_width=True)
with col2:
    send_recap = st.button("Send Recap Email", use_container_width=True)
with col3:
    send_digest = st.button("Send Weekly Digest", use_container_width=True, help="Sends new candidates to Brent & Thomas")


# --- Send weekly digest to Brent & Thomas ---
if send_digest:
    with st.spinner("Pulling latest candidates from database..."):
        try:
            new_candidates = get_new_since(days=7)
            all_candidates = get_all_candidates()
            if not new_candidates:
                st.info("No new candidates in the last 7 days — nothing to send.")
            else:
                for email in DIGEST_RECIPIENTS:
                    send_weekly_digest(
                        new_profiles=new_candidates,
                        all_profiles=all_candidates,
                        to_email=email,
                    )
                st.success(f"Weekly digest sent to Brent & Thomas! ({len(new_candidates)} new candidates)")
        except Exception as e:
            st.error(f"Failed: {e}")


# --- Send recap ---
if send_recap:
    last_results = load_last_results()
    last_recap = load_last_recap()
    if not last_results:
        st.warning("No scan results yet — run a scan first.")
    else:
        prev_handles = set(last_recap.get("handles", []))
        current_handles = set(p["handle"] for p in last_results)
        new_profiles = [p for p in last_results if p["handle"] not in prev_handles]
        removed_handles = prev_handles - current_handles

        with st.spinner("Sending recap..."):
            sent = send_recap_email(
                profiles=last_results,
                new_profiles=new_profiles,
                removed_handles=list(removed_handles),
                to_email=notify_email,
                frequency=recap_frequency,
            )
        if sent:
            save_last_recap({"sent_at": datetime.now().isoformat(), "handles": list(current_handles)})
            st.success(f"Recap sent to {notify_email}!")
        else:
            st.error("Failed to send recap — check your Resend API key.")


# --- Run scan ---
if run_scan:
    with st.spinner("Scanning GitHub... this takes 2-3 minutes"):
        if mode == "SF-Based AI Contributors":
            results = find_sf_ai_contributors(limit_per_repo=100)
        else:
            results = find_trending_repo_authors(days=7)

    if not results:
        st.warning("No profiles found.")
    else:
        seen = load_seen()
        prev_results = load_last_results()
        prev_handles = set(p["handle"] for p in prev_results)

        new_profiles = [p for p in results if p["handle"] not in seen]
        removed_handles = prev_handles - set(p["handle"] for p in results)

        seen.update(p["handle"] for p in results)
        save_seen(seen)
        save_last_results(results)

        # Save to Supabase
        with st.spinner("Saving to database..."):
            try:
                db_result = upsert_profiles(results)
                st.caption(f"💾 Saved to database — {len(db_result['new'])} new, {len(db_result['updated'])} updated")
            except Exception as e:
                st.caption(f"⚠️ Database save failed: {e}")

        # Auto-send notification for new profiles
        if new_profiles and notify_email:
            send_new_profiles_email(new_profiles, to_email=notify_email)

        # Auto-send recap if due
        last_recap = load_last_recap()
        if recap_due(recap_frequency, last_recap) and notify_email:
            sent = send_recap_email(
                profiles=results,
                new_profiles=new_profiles,
                removed_handles=list(removed_handles),
                to_email=notify_email,
                frequency=recap_frequency,
            )
            if sent:
                save_last_recap({"sent_at": datetime.now().isoformat(), "handles": [p["handle"] for p in results]})

        # --- Apply filters ---
        df = pd.DataFrame(results)

        if "founder_badges" not in df.columns:
            df["founder_badges"] = ""
        if "company" not in df.columns:
            df["company"] = ""
        df["founder_badges"] = df["founder_badges"].fillna("")
        df["bio"] = df["bio"].fillna("")
        df["location"] = df["location"].fillna("")
        df["company"] = df["company"].fillna("")

        # Score filter
        df = df[df["signal_score"] >= min_score]

        # Badge filter
        if filter_badges:
            df = df[df["founder_badges"].apply(lambda b: any(badge in b for badge in filter_badges))]

        import re

        # Min stars filter
        def total_stars(repos_str):
            stars = re.findall(r'\((\d+)⭐\)', repos_str or "")
            return sum(int(s) for s in stars)
        if min_stars > 0:
            df = df[df["top_repos"].apply(total_stars) >= min_stars]

        # Bio keyword filter
        if bio_keyword.strip():
            kw = bio_keyword.strip().lower()
            df = df[df["bio"].str.lower().str.contains(kw, na=False)]

        # Region filter
        if region.strip():
            r = region.strip().lower()
            df = df[df["location"].str.lower().str.contains(r, na=False)]

        # Account age filter
        if "account_age_years" in df.columns and max_account_age_years < 15:
            df = df[df["account_age_years"] <= max_account_age_years]

        # Stealth only
        if stealth_only:
            df = df[df["bio"].str.lower().str.contains("stealth", na=False)]

        # New only filter
        if track_mode:
            df = df[~df["handle"].isin(prev_handles)]

        # Sort
        if sort_by == "Followers":
            df = df.sort_values("followers", ascending=False)
        elif sort_by == "Repo Stars":
            import re
            df["_total_stars"] = df["top_repos"].apply(lambda r: sum(int(s) for s in re.findall(r'\((\d+)⭐\)', r or "")))
            df = df.sort_values("_total_stars", ascending=False)
        else:
            df = df.sort_values("signal_score", ascending=False)

        st.markdown(f"### Results — {len(df)} profiles")

        col_a, col_b, col_c = st.columns(3)
        col_a.metric("Total found", len(results))
        col_b.metric("New this scan", len(new_profiles))
        col_c.metric("Dropped off", len(removed_handles))

        if df.empty:
            st.info("No profiles match your current filters.")
        else:
            df["score_display"] = df["signal_score"].apply(lambda s: f"{score_color(s)} {s}")
            df["profile"] = df.apply(lambda r: f"[{r['handle']}]({r['github_url']})", axis=1)

            if "account_age_years" not in df.columns:
                df["account_age_years"] = None
            display_df = df[["profile", "name", "location", "company", "bio", "founder_badges", "account_age_years", "score_display", "top_repos"]].rename(columns={
                "profile": "GitHub",
                "name": "Name",
                "location": "Location",
                "company": "Company",
                "bio": "Bio",
                "founder_badges": "Signals",
                "account_age_years": "Acct Age (yrs)",
                "score_display": "Score",
                "top_repos": "Top Repos",
            })

            st.dataframe(
                display_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "GitHub": st.column_config.LinkColumn("GitHub"),
                }
            )

            csv = df.to_csv(index=False)
            st.download_button(
                "Download CSV",
                data=csv,
                file_name=f"m13_sourcing_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
            )

st.markdown("---")
st.caption(f"Built for M13 · Powered by GitHub API · Last updated {datetime.now().strftime('%b %d, %Y')}")
