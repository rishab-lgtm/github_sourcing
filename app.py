"""
M13 GitHub Sourcing — Streamlit UI
"""

import os
import json
import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from datetime import datetime
from notifications import send_new_profiles_email
from github_sourcing import find_sf_ai_contributors, find_trending_repo_authors

load_dotenv()

SEEN_FILE = "output/seen_profiles.json"

st.set_page_config(page_title="M13 GitHub Sourcing", page_icon="🔍", layout="wide")

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');
    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
    .stButton > button { border-radius: 6px; font-weight: 500; }
    .score-high { color: #16a34a; font-weight: 600; }
    .score-mid  { color: #d97706; font-weight: 600; }
    .score-low  { color: #6b7280; }
</style>
""", unsafe_allow_html=True)


def load_seen() -> set:
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set):
    os.makedirs("output", exist_ok=True)
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)


def score_color(score):
    if score >= 60:
        return "🟢"
    elif score >= 50:
        return "🟡"
    return "🔴"


# --- Sidebar ---
st.sidebar.image("https://upload.wikimedia.org/wikipedia/commons/thumb/a/a7/M13_galaxy.jpg/100px-M13_galaxy.jpg", width=40)
st.sidebar.title("GitHub Sourcing")
st.sidebar.markdown("Surface high-signal engineers before they're on anyone's radar.")
st.sidebar.markdown("---")

mode = st.sidebar.radio("Filter", ["SF-Based AI Contributors", "Trending Repo Authors"])
st.sidebar.markdown("---")

notify_email = st.sidebar.text_input("Notify email", value="rishab@m13.co")
track_mode = st.sidebar.checkbox("Only show new profiles", value=False, help="Hides profiles you've already seen in previous runs")

st.sidebar.markdown("---")
st.sidebar.markdown("**About**")
st.sidebar.caption("Scans top AI repos on GitHub for Bay Area engineers and scores them by signal strength.")


# --- Main ---
st.title("🔍 M13 GitHub Sourcing")
st.markdown("Find talented engineers and future founders before they're on AngelList or TechCrunch.")
st.markdown("---")

if st.button("Run Scan", type="primary", use_container_width=True):
    with st.spinner("Scanning GitHub..."):
        if mode == "SF-Based AI Contributors":
            results = find_sf_ai_contributors(limit_per_repo=100)
        else:
            results = find_trending_repo_authors(days=7)

    if not results:
        st.warning("No profiles found. Try expanding the filters.")
    else:
        seen = load_seen()
        new_profiles = [p for p in results if p["handle"] not in seen]
        returning = [p for p in results if p["handle"] in seen]

        # Save seen
        seen.update(p["handle"] for p in results)
        save_seen(seen)

        # Send notification for new profiles
        if new_profiles and notify_email:
            sent = send_new_profiles_email(new_profiles, to_email=notify_email)
            if sent:
                st.success(f"Email sent to {notify_email} with {len(new_profiles)} new profiles!")

        display = new_profiles if track_mode else results

        st.markdown(f"### Results — {len(display)} profiles {'(new only)' if track_mode else ''}")
        if track_mode:
            st.caption(f"{len(new_profiles)} new · {len(returning)} already seen")

        # Build display dataframe
        df = pd.DataFrame(display)
        if df.empty:
            st.info("No new profiles since last run." if track_mode else "No profiles found.")
        else:
            df["score"] = df["signal_score"].apply(lambda s: f"{score_color(s)} {s}")
            df["profile"] = df.apply(lambda r: f"[{r['handle']}]({r['github_url']})", axis=1)
            df["repos"] = df["top_repos"]
            if "founder_badges" not in df.columns:
                df["founder_badges"] = ""
            df["founder_badges"] = df["founder_badges"].fillna("")
            if "company" not in df.columns:
                df["company"] = ""

            display_df = df[["profile", "name", "location", "company", "bio", "founder_badges", "score", "repos"]].rename(columns={
                "profile": "GitHub",
                "name": "Name",
                "location": "Location",
                "company": "Company",
                "bio": "Bio",
                "founder_badges": "Signals",
                "score": "Signal Score",
                "repos": "Top Repos",
            })

            st.dataframe(
                display_df,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "GitHub": st.column_config.LinkColumn("GitHub"),
                    "Signal Score": st.column_config.TextColumn("Signal Score"),
                }
            )

            # Export
            csv = df.to_csv(index=False)
            st.download_button(
                "Download CSV",
                data=csv,
                file_name=f"m13_sourcing_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv",
            )

st.markdown("---")
st.caption("Built for M13 · Powered by GitHub API")
