"""
M13 GitHub Sourcing — Streamlit UI
"""

from __future__ import annotations

import re
import logging
import pandas as pd
import streamlit as st
from datetime import datetime, timedelta

from streamlit_auth import check_auth, show_login_page, logout
from notifications import send_new_profiles_email, send_recap_email, send_weekly_digest
from github_sourcing import (
    find_sf_ai_contributors,
    find_trending_repo_authors,
    search_by_intent,
    expand_query,
    get_session_request_count,
    SESSION_REQUEST_LIMIT,
)
from database import (
    upsert_user,
    upsert_profiles,
    get_all_candidates,
    get_new_since,
    create_search_run,
    update_search_run_count,
    record_matches,
    record_snapshots,
    get_run_history,
    save_search,
    get_saved_searches,
    delete_saved_search,
    get_prior_handles_for_search,
    get_notification_prefs,
    save_notification_prefs,
    get_breakout_candidates,
    get_snapshots_for_handle,
    load_user_results,
    save_user_results,
    load_user_recap,
    save_user_recap,
    audit,
)
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)

DIGEST_RECIPIENTS = ["brent@m13.co", "thomas@m13.co"]

st.set_page_config(page_title="M13 GitHub Sourcing", page_icon="⚡", layout="wide")

# ── Auth gate ─────────────────────────────────────────────────────────────────
if not check_auth():
    show_login_page()
    st.stop()

USER_EMAIL: str = st.session_state.get("user_email", "")
USER_NAME: str = st.session_state.get("user_name", USER_EMAIL)

# Register/update user on each login
if not st.session_state.get("_user_registered"):
    upsert_user(USER_EMAIL, USER_NAME)
    st.session_state["_user_registered"] = True

# ── Styles ────────────────────────────────────────────────────────────────────
st.markdown(
    '<link rel="preconnect" href="https://fonts.googleapis.com">'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
    '<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">',
    unsafe_allow_html=True,
)
st.markdown("""
<style>
    :root { --space-blue:#150F3A; --wave-blue:#0083FF; --gray:#737368; --white:#FFFFFF; --off-white:#F7F7F8; --border:#E8E8EC; }
    html, body, [class*="css"], .stApp, .stApp * { font-family:'Poppins',-apple-system,BlinkMacSystemFont,sans-serif !important; -webkit-font-smoothing:antialiased; }
    body, .stApp { background:var(--off-white) !important; }
    #MainMenu, footer, header { visibility:hidden; }
    .block-container { padding-top:1.5rem; padding-bottom:2rem; }

    [data-testid="stSidebar"] { background:var(--space-blue); border-right:1px solid rgba(255,255,255,0.08); }
    [data-testid="stSidebar"] * { color:rgba(255,255,255,0.85) !important; }
    [data-testid="stSidebar"] label { color:rgba(255,255,255,0.5) !important; font-size:0.65rem !important; font-weight:600 !important; letter-spacing:0.1em !important; text-transform:uppercase !important; }
    [data-testid="stSidebar"] hr { border-color:rgba(255,255,255,0.1) !important; margin:1rem 0 !important; }
    [data-testid="stSidebar"] .stRadio label, [data-testid="stSidebar"] .stCheckbox label { font-size:0.83rem !important; text-transform:none !important; letter-spacing:0 !important; }
    [data-testid="stSidebar"] input, [data-testid="stSidebar"] .stSelectbox > div, [data-testid="stSidebar"] div[data-baseweb="select"] > div:first-child { background:rgba(255,255,255,0.07) !important; border-color:rgba(255,255,255,0.15) !important; border-radius:8px !important; }
    [data-testid="stSidebar"] span[data-baseweb="tag"] { background:rgba(0,131,255,0.2) !important; border-radius:999px !important; }

    .stButton > button { border-radius:999px !important; font-weight:600 !important; font-size:0.82rem !important; padding:0.5rem 1.25rem !important; transition:all 0.14s ease !important; border:1.5px solid var(--border) !important; background:var(--white) !important; color:var(--space-blue) !important; box-shadow:0 1px 2px rgba(21,15,58,0.06) !important; }
    .stButton > button:hover { border-color:#c5c5d0 !important; box-shadow:0 3px 8px rgba(21,15,58,0.1) !important; transform:translateY(-1px) !important; }
    .stButton > button[kind="primary"] { background:var(--wave-blue) !important; border-color:var(--wave-blue) !important; color:#fff !important; box-shadow:0 2px 8px rgba(0,131,255,0.35) !important; }
    .stButton > button[kind="primary"]:hover { background:#006FDB !important; border-color:#006FDB !important; box-shadow:0 4px 14px rgba(0,131,255,0.45) !important; }
    .stButton > button[kind="primary"] p { color:#fff !important; }

    [data-testid="metric-container"] { background:white; border-radius:14px; padding:1rem 1.25rem; border:1px solid var(--border); box-shadow:0 2px 8px rgba(21,15,58,0.05); }
    [data-testid="stMetricValue"] { font-size:2rem !important; font-weight:800 !important; color:var(--space-blue) !important; letter-spacing:-0.04em !important; }
    [data-testid="stMetricLabel"] { font-size:0.68rem !important; font-weight:600 !important; color:var(--gray) !important; text-transform:uppercase !important; letter-spacing:0.07em !important; }

    .profile-card { background:white; border-radius:16px; padding:1.25rem 1.5rem; border:1px solid var(--border); box-shadow:0 2px 12px rgba(21,15,58,0.05); margin-bottom:0.75rem; }
    .profile-name { font-size:1rem; font-weight:700; color:var(--space-blue); }
    .profile-handle { font-size:0.78rem; color:var(--wave-blue); font-weight:600; text-decoration:none; }
    .profile-meta { font-size:0.78rem; color:rgba(21,15,58,0.5); margin-top:0.25rem; }
    .profile-bio { font-size:0.82rem; color:rgba(21,15,58,0.7); margin-top:0.5rem; line-height:1.5; font-style:italic; }
    .badge { display:inline-block; background:rgba(0,131,255,0.1); color:#0055B3; border-radius:20px; padding:0.15rem 0.6rem; font-size:0.72rem; font-weight:600; margin-right:0.3rem; margin-top:0.4rem; }
    .score-pill { display:inline-block; border-radius:20px; padding:0.2rem 0.75rem; font-size:0.75rem; font-weight:700; float:right; }
    .score-high { background:#d4f7e8; color:#0a6641; }
    .score-mid  { background:#fff8d4; color:#7a6000; }
    .score-low  { background:#fde8e8; color:#8b1a1a; }
    .reason-tag { display:inline-block; background:rgba(21,15,58,0.05); color:rgba(21,15,58,0.6); border-radius:8px; padding:0.1rem 0.5rem; font-size:0.68rem; margin-right:0.25rem; margin-top:0.3rem; }
    .repo-tag { display:inline-block; background:rgba(0,131,255,0.06); color:#0055B3; border-radius:8px; padding:0.1rem 0.5rem; font-size:0.7rem; margin-right:0.25rem; margin-top:0.3rem; }
    .new-badge { display:inline-block; background:#0083FF; color:white; border-radius:6px; padding:0.1rem 0.45rem; font-size:0.62rem; font-weight:700; letter-spacing:0.05em; text-transform:uppercase; vertical-align:middle; margin-left:0.4rem; }
    .section-header { font-size:1.1rem; font-weight:700; color:var(--space-blue); margin:1.5rem 0 1rem; padding-bottom:0.5rem; border-bottom:2px solid rgba(0,131,255,0.15); }
    .intent-preview { background:rgba(0,131,255,0.05); border:1px solid rgba(0,131,255,0.15); border-radius:10px; padding:0.6rem 1rem; font-size:0.78rem; color:rgba(21,15,58,0.7); margin-bottom:1rem; }
    .saved-card { background:white; border-radius:12px; padding:1rem 1.25rem; border:1px solid var(--border); margin-bottom:0.6rem; box-shadow:0 1px 4px rgba(21,15,58,0.04); }
    .history-row { background:white; border-radius:10px; padding:0.7rem 1rem; border:1px solid var(--border); margin-bottom:0.4rem; font-size:0.83rem; }
    .stDownloadButton > button { border-radius:999px !important; font-weight:600 !important; font-size:0.8rem !important; background:var(--white) !important; color:var(--space-blue) !important; border:1.5px solid var(--border) !important; }
    .stDownloadButton > button:hover { border-color:var(--wave-blue) !important; color:var(--wave-blue) !important; }
    .stAlert { border-radius:10px !important; font-size:0.875rem !important; border:none !important; }
    .stTextInput > div > div > input, .stTextArea > div > div > textarea { border-radius:10px !important; border:1.5px solid var(--border) !important; background:var(--white) !important; font-size:0.875rem !important; color:var(--space-blue) !important; }
    .stTextInput > div > div > input:focus, .stTextArea > div > div > textarea:focus { border-color:var(--wave-blue) !important; box-shadow:0 0 0 3px rgba(0,131,255,0.12) !important; }
    .stTabs [data-baseweb="tab"] { font-size:0.83rem !important; font-weight:600 !important; }
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def total_stars(repos_str: str) -> int:
    return sum(int(s) for s in re.findall(r'\((\d+)⭐\)', repos_str or ""))


def score_class(score: int) -> str:
    if score >= 60: return "score-high"
    if score >= 50: return "score-mid"
    return "score-low"


def recap_due(frequency: str, last_recap: dict) -> bool:
    if not last_recap.get("sent_at"):
        return True
    last = datetime.fromisoformat(last_recap["sent_at"])
    delta = {"Daily": 1, "Bi-Daily": 2, "Weekly": 7, "Monthly": 30}[frequency]
    return datetime.now() >= last + timedelta(days=delta)


def render_profile_card(row: dict, is_new: bool = False):
    handle = row.get("handle", "")
    name = row.get("name") or handle
    github_url = row.get("github_url", f"https://github.com/{handle}")
    location = row.get("location", "")
    company = row.get("company", "")
    bio = row.get("bio", "")
    badges = row.get("founder_badges", "")
    score = row.get("signal_score", 0)
    top_repos = row.get("top_repos", "")
    acct_age = row.get("account_age_years")
    reasons = row.get("match_reasons") or []

    meta_parts = []
    if location: meta_parts.append(f"📍 {location}")
    if company: meta_parts.append(f"🏢 {company}")
    if acct_age is not None: meta_parts.append(f"⏱ {acct_age}yr account")

    badge_html = "".join(
        f'<span class="badge">{b.strip()}</span>'
        for b in str(badges).split("|") if b.strip()
    )
    repo_items = [r.strip() for r in str(top_repos).split(",") if r.strip()][:3]
    repos_html = "".join(f'<span class="repo-tag">{r}</span>' for r in repo_items)
    reasons_html = "".join(f'<span class="reason-tag">✓ {r}</span>' for r in reasons[:5])
    new_html = '<span class="new-badge">New</span>' if is_new else ""
    bio_html = f'<div class="profile-bio">"{bio}"</div>' if bio and bio not in ("nan", "") else ""

    st.markdown(f"""
    <div class="profile-card">
        <div>
            <span class="score-pill {score_class(score)}">{score}</span>
            <div class="profile-name">{name}{new_html}</div>
            <a class="profile-handle" href="{github_url}" target="_blank">@{handle}</a>
        </div>
        <div class="profile-meta">{" · ".join(meta_parts)}</div>
        {bio_html}
        <div style="margin-top:0.5rem">{badge_html}</div>
        <div style="margin-top:0.25rem">{reasons_html}</div>
        <div style="margin-top:0.25rem">{repos_html}</div>
    </div>
    """, unsafe_allow_html=True)


def apply_filters(df: pd.DataFrame, prev_handles: set = None,
                  min_score: int = 0, filter_badges: list = None,
                  min_stars: int = 0, bio_keyword: str = "",
                  region: str = "", max_account_age: int = 15,
                  stealth_only: bool = False, show_new_only: bool = False,
                  sort_by: str = "Signal Score") -> pd.DataFrame:
    for col in ["founder_badges", "company", "bio", "location", "top_repos"]:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("")

    df = df[df["signal_score"] >= min_score]

    if filter_badges:
        df = df[df["founder_badges"].apply(lambda b: any(badge in b for badge in filter_badges))]
    if min_stars > 0:
        df = df[df["top_repos"].apply(total_stars) >= min_stars]
    if bio_keyword.strip():
        df = df[df["bio"].str.lower().str.contains(bio_keyword.strip().lower(), na=False)]
    if region.strip():
        df = df[df["location"].str.lower().str.contains(region.strip().lower(), na=False)]
    if "account_age_years" in df.columns and max_account_age < 15:
        df = df[df["account_age_years"] <= max_account_age]
    if stealth_only:
        df = df[df["bio"].str.lower().str.contains("stealth", na=False)]
    if show_new_only and prev_handles:
        df = df[~df["handle"].isin(prev_handles)]

    if sort_by == "Followers" and "followers" in df.columns:
        df = df.sort_values("followers", ascending=False)
    elif sort_by == "Repo Stars":
        df = df.copy()
        df["_stars"] = df["top_repos"].apply(total_stars)
        df = df.sort_values("_stars", ascending=False)
    else:
        df = df.sort_values("signal_score", ascending=False)

    return df


def run_search(mode: str, intent: str, region: str, saved_search_id: str = None,
               triggered_by: str = "manual") -> list:
    if mode == "Intent Search":
        return search_by_intent(intent=intent.strip(), location=region.strip(), max_results=60)
    elif mode == "SF-Based AI Contributors":
        return find_sf_ai_contributors(limit_per_repo=30)
    else:
        return find_trending_repo_authors(days=7)


def display_results(results: list, prev_handles: set,
                    min_score: int, filter_badges: list, min_stars: int,
                    bio_keyword: str, region: str, max_account_age: int,
                    stealth_only: bool, show_new_only: bool, sort_by: str):
    new_handle_set = {p["handle"] for p in results if p["handle"] not in prev_handles}
    removed = len(prev_handles - {p["handle"] for p in results})

    df = apply_filters(
        pd.DataFrame(results), prev_handles=prev_handles,
        min_score=min_score, filter_badges=filter_badges, min_stars=min_stars,
        bio_keyword=bio_keyword, region=region, max_account_age=max_account_age,
        stealth_only=stealth_only, show_new_only=show_new_only, sort_by=sort_by,
    )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Found", len(results))
    m2.metric("After Filters", len(df))
    m3.metric("New This Run", len(new_handle_set))
    m4.metric("Dropped Off", removed)

    if df.empty:
        st.info("No profiles match your filters. Try lowering the score threshold.")
        return

    st.markdown(f'<div class="section-header">Results — {len(df)} profiles</div>', unsafe_allow_html=True)
    for _, row in df.iterrows():
        render_profile_card(row.to_dict(), is_new=row.get("handle") in new_handle_set)

    st.markdown("")
    csv_data = df.drop(columns=["_stars"], errors="ignore").to_csv(index=False)
    st.download_button(
        "⬇  Download CSV", data=csv_data,
        file_name=f"m13_sourcing_{datetime.now().strftime('%Y%m%d')}.csv",
        mime="text/csv",
    )


# ── Load notification prefs ───────────────────────────────────────────────────
prefs = get_notification_prefs(USER_EMAIL)


# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(f"""
    <div style="padding:0.5rem 0 1rem">
        <div style="font-size:1.1rem;font-weight:700;">⚡ GitHub Sourcing</div>
        <div style="font-size:0.72rem;opacity:0.5;margin-top:0.2rem;">Surface engineers before they're on anyone's radar</div>
    </div>
    """, unsafe_allow_html=True)
    st.markdown("---")

    st.markdown("#### Scan Mode")
    mode = st.radio("", ["Intent Search", "SF-Based AI Contributors", "Trending Repo Authors"],
                    label_visibility="collapsed")

    st.markdown("---")
    st.markdown("#### Filters")
    min_score   = st.slider("Min signal score", 0, 100, 40, step=5)
    filter_badges = st.multiselect("Founder signals",
                                   ["🏛 Top Lab", "🚀 Building", "🔬 Researcher"], default=[])
    min_stars   = st.number_input("Min repo stars", min_value=0, value=0, step=50)
    bio_keyword = st.text_input("Bio keyword", placeholder="stealth, agent, infra…")
    region      = st.text_input("Region", placeholder="SF, NYC, London — blank for all")
    max_account_age = st.slider("Max account age (yrs)", 1, 15, 15)
    stealth_only    = st.checkbox("Stealth only", value=False)
    show_new_only   = st.checkbox("Only show new profiles", value=False)
    sort_by         = st.selectbox("Sort by", ["Signal Score", "Followers", "Repo Stars"])

    # Rate limit indicator
    used = get_session_request_count()
    pct = int(used / SESSION_REQUEST_LIMIT * 100)
    if pct > 60:
        st.markdown(f"<div style='font-size:0.72rem;color:rgba(255,200,0,0.8)'>⚠ API usage: {used}/{SESSION_REQUEST_LIMIT} requests this session</div>", unsafe_allow_html=True)

    st.markdown("---")
    st.markdown(f"""
    <div style="font-size:0.72rem;opacity:0.6;margin-bottom:0.5rem;">
        Signed in as<br><strong style="opacity:1">{USER_NAME}</strong>
    </div>
    """, unsafe_allow_html=True)
    if st.button("Sign out"):
        audit(USER_EMAIL, "logout")
        logout()
        st.rerun()
    st.caption("Built for M13 · GitHub API")


# ── Header ────────────────────────────────────────────────────────────────────
st.markdown("""
<div style="background:linear-gradient(135deg,#150F3A 0%,#1e1660 100%);border-radius:18px;
            padding:1.75rem 2rem;margin-bottom:1.75rem;">
    <div style="color:rgba(255,255,255,0.5);font-size:0.65rem;font-weight:600;
                letter-spacing:0.15em;text-transform:uppercase;margin-bottom:0.3rem;">M13 Internal</div>
    <div style="color:white;font-size:1.6rem;font-weight:700;letter-spacing:-0.02em;line-height:1.1;">GitHub Sourcing</div>
    <div style="color:rgba(255,255,255,0.55);font-size:0.82rem;margin-top:0.4rem;">
        Find talented engineers and future founders before they appear on AngelList or TechCrunch.
    </div>
</div>
""", unsafe_allow_html=True)


# ── Tabs ──────────────────────────────────────────────────────────────────────
tab_search, tab_saved, tab_breakout, tab_history, tab_settings = st.tabs(
    ["🔍 Search", "⭐ Saved Searches", "🚀 Breakouts", "📋 History", "⚙️ Settings"]
)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB: Search
# ═══════════════════════════════════════════════════════════════════════════════
with tab_search:
    intent = ""
    if mode == "Intent Search":
        st.markdown("#### What are you looking for?")
        intent = st.text_input(
            "", placeholder='"biotech AI researcher", "climate infra engineer", "stealth AI founder"',
            label_visibility="collapsed",
        )
        if intent:
            expanded = expand_query(intent)
            top_terms = sorted(expanded, key=len, reverse=True)[:10]
            st.markdown(
                f'<div class="intent-preview">🔍 Searching for: <strong>{" · ".join(top_terms)}</strong></div>',
                unsafe_allow_html=True,
            )
        st.markdown("")

    c1, c2, c3 = st.columns([3, 1.4, 1.4])
    with c1:
        label = "⚡  Search" if mode == "Intent Search" else "⚡  Run Scan"
        run_btn = st.button(label, type="primary", use_container_width=True,
                            disabled=(mode == "Intent Search" and not intent.strip()))
    with c2:
        recap_btn = st.button("📨  Send Recap", use_container_width=True)
    with c3:
        digest_btn = st.button("📬  Weekly Digest", use_container_width=True)

    notify_email = prefs.get("notify_email") or USER_EMAIL
    recap_frequency = prefs.get("recap_frequency", "Weekly")

    if digest_btn:
        with st.spinner("Pulling candidates from database..."):
            try:
                new_candidates = get_new_since(days=7)
                all_candidates = get_all_candidates()
                if not new_candidates:
                    st.info("No new candidates in the last 7 days.")
                else:
                    for email in DIGEST_RECIPIENTS:
                        send_weekly_digest(new_profiles=new_candidates, all_profiles=all_candidates, to_email=email)
                    audit(USER_EMAIL, "digest_sent", {"count": len(new_candidates)})
                    st.success(f"Weekly digest sent to Brent & Thomas — {len(new_candidates)} new candidates.")
            except Exception as e:
                st.error(f"Failed: {e}")

    if recap_btn:
        last_results = load_user_results(USER_EMAIL)
        last_recap = load_user_recap(USER_EMAIL)
        if not last_results:
            st.warning("No scan results yet — run a scan first.")
        else:
            prev_handles = set(last_recap.get("handles", []))
            current_handles = {p["handle"] for p in last_results}
            new_profiles = [p for p in last_results if p["handle"] not in prev_handles]
            removed_handles = list(prev_handles - current_handles)
            with st.spinner("Sending recap..."):
                sent = send_recap_email(
                    profiles=last_results, new_profiles=new_profiles,
                    removed_handles=removed_handles, to_email=notify_email,
                    frequency=recap_frequency,
                )
            if sent:
                save_user_recap(USER_EMAIL, {"sent_at": datetime.now().isoformat(), "handles": list(current_handles)})
                audit(USER_EMAIL, "recap_sent", {"to": notify_email, "count": len(last_results)})
                st.success(f"Recap sent to {notify_email}!")
            else:
                st.error("Failed — check your Resend API key.")

    if run_btn:
        scan_label = intent.strip() if mode == "Intent Search" else mode
        with st.spinner(f"Searching GitHub for '{scan_label}'… this takes 2–3 minutes"):
            results = run_search(mode, intent, region)

        if not results:
            st.warning("No profiles found. Try broadening your search.")
        else:
            run_id = create_search_run(
                user_email=USER_EMAIL, intent=scan_label, mode=mode,
                filters={"min_score": min_score, "region": region, "bio_keyword": bio_keyword},
            )
            prev_results = load_user_results(USER_EMAIL)
            prev_handles = {p["handle"] for p in prev_results}
            new_profiles = [p for p in results if p["handle"] not in prev_handles]

            save_user_results(USER_EMAIL, results)

            with st.spinner("Saving to database..."):
                try:
                    db_result = upsert_profiles(results)
                    record_matches(run_id, results, source_query=scan_label)
                    record_snapshots(results)
                    update_search_run_count(run_id, len(results))
                    audit(USER_EMAIL, "search_run", {"intent": scan_label, "mode": mode, "count": len(results)})
                    st.caption(f"Saved — {len(db_result['new'])} new, {len(db_result['updated'])} updated")
                except Exception as e:
                    st.caption(f"Database save failed: {e}")

            if new_profiles and notify_email and prefs.get("notify_on_new_match", True):
                send_new_profiles_email(new_profiles, to_email=notify_email)

            last_recap = load_user_recap(USER_EMAIL)
            if recap_due(recap_frequency, last_recap) and notify_email:
                removed_handles = list(prev_handles - {p["handle"] for p in results})
                sent = send_recap_email(
                    profiles=results, new_profiles=new_profiles,
                    removed_handles=removed_handles, to_email=notify_email,
                    frequency=recap_frequency,
                )
                if sent:
                    save_user_recap(USER_EMAIL, {
                        "sent_at": datetime.now().isoformat(),
                        "handles": [p["handle"] for p in results],
                    })

            display_results(
                results, prev_handles,
                min_score, filter_badges, min_stars, bio_keyword, region,
                max_account_age, stealth_only, show_new_only, sort_by,
            )

            # Offer to save this search
            with st.expander("💾 Save this search"):
                save_name = st.text_input("Search name", value=scan_label[:60], key="save_name")
                notify_toggle = st.checkbox("Notify me when new candidates match", value=True, key="save_notify")
                if st.button("Save Search", key="do_save"):
                    if save_name.strip():
                        save_search(
                            user_email=USER_EMAIL,
                            name=save_name.strip(),
                            intent=scan_label,
                            mode=mode,
                            filters={"region": region, "bio_keyword": bio_keyword},
                            notify_on_new=notify_toggle,
                        )
                        st.success(f"Saved as '{save_name}'! It will auto-run via the scheduler.")
                    else:
                        st.warning("Enter a name first.")
    else:
        # Show cached results
        last_results = load_user_results(USER_EMAIL)
        if not last_results:
            st.markdown("""
            <div style="text-align:center;padding:4rem 1rem;color:rgba(21,15,58,0.4);">
                <div style="font-size:3rem;margin-bottom:0.75rem;">⚡</div>
                <p style="font-size:1rem;font-weight:600;color:rgba(21,15,58,0.6);">No scan run yet</p>
                <p>Choose a mode and hit <strong>Run Scan</strong> to surface high-signal engineers.</p>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.info("Showing your cached results from last scan. Hit **Run Scan** to refresh.")
            display_results(
                last_results, set(),
                min_score, filter_badges, min_stars, bio_keyword, region,
                max_account_age, stealth_only, show_new_only, sort_by,
            )


# ═══════════════════════════════════════════════════════════════════════════════
# TAB: Saved Searches
# ═══════════════════════════════════════════════════════════════════════════════
with tab_saved:
    st.markdown('<div class="section-header">Your Saved Searches</div>', unsafe_allow_html=True)
    st.markdown("""
    <p style="font-size:0.83rem;color:rgba(21,15,58,0.6);margin-top:-0.5rem;margin-bottom:1rem;">
        Saved searches run automatically via the scheduler (<code>python3 scheduler.py</code>) and notify you when new candidates match.
    </p>
    """, unsafe_allow_html=True)

    saved = get_saved_searches(USER_EMAIL)

    if not saved:
        st.markdown("""
        <div style="text-align:center;padding:3rem 1rem;color:rgba(21,15,58,0.4);">
            <div style="font-size:2.5rem;margin-bottom:0.5rem;">⭐</div>
            <p>No saved searches yet.<br>Run a search and click <strong>Save this search</strong> to get started.</p>
        </div>
        """, unsafe_allow_html=True)
    else:
        for s in saved:
            last_run = s.get("last_run_at", "")[:10] if s.get("last_run_at") else "Never"
            notify_icon = "🔔" if s.get("notify_on_new") else "🔕"
            col_a, col_b, col_c = st.columns([5, 1.2, 1])
            with col_a:
                st.markdown(f"""
                <div class="saved-card">
                    <div style="font-weight:700;color:#150F3A;font-size:0.95rem;">{s['name']}</div>
                    <div style="font-size:0.75rem;color:#737368;margin-top:0.2rem;">
                        {s['mode']} · {notify_icon} notify on new · Last run: {last_run} · {s.get('last_result_count',0)} results
                    </div>
                    <div style="font-size:0.78rem;color:#0083FF;margin-top:0.3rem;">"{s['intent']}"</div>
                </div>
                """, unsafe_allow_html=True)
            with col_b:
                if st.button("▶ Run now", key=f"run_{s['search_id']}"):
                    with st.spinner(f"Running '{s['name']}'…"):
                        res = run_search(s["mode"], s["intent"], s.get("filters", {}).get("region", ""))
                    if res:
                        prior = get_prior_handles_for_search(s["search_id"])
                        new_p = [p for p in res if p["handle"] not in prior]
                        run_id = create_search_run(
                            user_email=USER_EMAIL, intent=s["intent"], mode=s["mode"],
                            filters=s.get("filters") or {}, saved_search_id=s["search_id"],
                        )
                        upsert_profiles(res)
                        record_matches(run_id, res, source_query=s["intent"])
                        update_search_run_count(run_id, len(res))
                        from database import update_saved_search_last_run
                        update_saved_search_last_run(s["search_id"], len(res))
                        st.success(f"{len(res)} results — {len(new_p)} new")
                        if new_p and s.get("notify_on_new"):
                            send_new_profiles_email(new_p, to_email=prefs.get("notify_email") or USER_EMAIL)
                            audit(USER_EMAIL, "notification_sent", {"search": s["name"], "new_count": len(new_p)})
                    else:
                        st.warning("No results.")
            with col_c:
                if st.button("🗑 Delete", key=f"del_{s['search_id']}"):
                    delete_saved_search(s["search_id"], USER_EMAIL)
                    st.rerun()

    st.markdown("---")
    st.markdown("#### Add New Saved Search")
    with st.form("new_saved_search"):
        ns_name   = st.text_input("Name", placeholder="e.g. Biotech AI Researchers")
        ns_intent = st.text_input("Search intent", placeholder="biotech AI researcher")
        ns_mode   = st.selectbox("Mode", ["Intent Search", "SF-Based AI Contributors", "Trending Repo Authors"])
        ns_region = st.text_input("Region filter", placeholder="SF, NYC — blank for all")
        ns_notify = st.checkbox("Notify me when new candidates match", value=True)
        submitted = st.form_submit_button("Save Search", type="primary")
        if submitted:
            if ns_name.strip() and ns_intent.strip():
                save_search(
                    user_email=USER_EMAIL, name=ns_name.strip(),
                    intent=ns_intent.strip(), mode=ns_mode,
                    filters={"region": ns_region.strip()},
                    notify_on_new=ns_notify,
                )
                st.success(f"Saved '{ns_name}'!")
                st.rerun()
            else:
                st.warning("Name and intent are required.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB: Breakouts
# ═══════════════════════════════════════════════════════════════════════════════
with tab_breakout:
    st.markdown('<div class="section-header">🚀 Breakout Candidates</div>', unsafe_allow_html=True)
    st.markdown("""
    <p style="font-size:0.83rem;color:rgba(21,15,58,0.6);margin-top:-0.5rem;margin-bottom:1rem;">
        GitHub profiles that have 3x'd their followers or repo stars recently —
        people gaining momentum fast, before they're on anyone's radar.
        Snapshots are recorded on every scan run; accuracy improves over time.
    </p>
    """, unsafe_allow_html=True)

    bo_col1, bo_col2, bo_col3 = st.columns([1, 1, 2])
    with bo_col1:
        bo_days = st.selectbox("Lookback window", [7, 14, 30, 60, 90], index=2,
                               format_func=lambda d: f"Last {d} days")
    with bo_col2:
        bo_mult = st.selectbox("Min growth multiplier", [2.0, 3.0, 5.0, 10.0], index=1,
                               format_func=lambda m: f"{m}x")
    with bo_col3:
        bo_min_abs = st.number_input("Min absolute follower/star gain", min_value=0, value=100, step=50)

    bo_refresh = st.button("🔄 Refresh", key="bo_refresh")

    if "breakout_data" not in st.session_state or bo_refresh:
        with st.spinner("Scanning for breakout candidates…"):
            st.session_state["breakout_data"] = get_breakout_candidates(
                days=bo_days, min_multiplier=bo_mult, min_abs_growth=int(bo_min_abs)
            )

    breakouts = st.session_state.get("breakout_data", [])

    if not breakouts:
        st.markdown("""
        <div style="text-align:center;padding:3rem 1rem;color:rgba(21,15,58,0.4);">
            <div style="font-size:2.5rem;margin-bottom:0.5rem;">📈</div>
            <p>No breakout candidates found with these settings.<br>
            Try a longer lookback window or lower multiplier.<br>
            <em>Breakouts require at least 2 snapshot runs to compute velocity.</em></p>
        </div>
        """, unsafe_allow_html=True)
    else:
        bm1, bm2, bm3 = st.columns(3)
        bm1.metric("Breakout Candidates", len(breakouts))
        bm2.metric("Fastest Follower Growth",
                   f"{max(b['follower_mult'] for b in breakouts)}x",
                   delta=f"+{max(b['follower_gain'] for b in breakouts):,} followers")
        bm3.metric("Fastest Star Growth",
                   f"{max(b['star_mult'] for b in breakouts)}x",
                   delta=f"+{max(b['star_gain'] for b in breakouts):,} stars")

        st.markdown("")

        for b in breakouts:
            handle = b["handle"]
            url = b["github_url"]
            f_mult = b["follower_mult"]
            s_mult = b["star_mult"]
            signal = "🚀 Follower surge" if f_mult >= s_mult else "⭐ Star surge"
            top_mult = max(f_mult, s_mult)
            score_delta = b["score_delta"]
            delta_str = f"+{score_delta}" if score_delta >= 0 else str(score_delta)
            delta_color = "#16a34a" if score_delta >= 0 else "#dc2626"

            st.markdown(f"""
            <div class="profile-card">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;">
                    <div>
                        <span class="score-pill {'score-high' if top_mult >= 5 else 'score-mid'}">{top_mult}x</span>
                        <div class="profile-name">
                            <a class="profile-handle" href="{url}" target="_blank">@{handle}</a>
                            &nbsp;<span style="font-size:0.78rem;color:#737368;">{signal}</span>
                        </div>
                    </div>
                </div>
                <div style="display:flex;gap:2rem;margin-top:0.75rem;flex-wrap:wrap;">
                    <div>
                        <div style="font-size:0.68rem;text-transform:uppercase;letter-spacing:0.07em;color:#737368;">Followers</div>
                        <div style="font-weight:700;color:#150F3A;">{b['followers_now']:,}
                            <span style="font-size:0.75rem;color:#16a34a;">↑{b['follower_gain']:,} ({f_mult}x)</span>
                        </div>
                        <div style="font-size:0.72rem;color:#737368;">was {b['followers_then']:,}</div>
                    </div>
                    <div>
                        <div style="font-size:0.68rem;text-transform:uppercase;letter-spacing:0.07em;color:#737368;">Repo Stars</div>
                        <div style="font-weight:700;color:#150F3A;">{b['stars_now']:,}
                            <span style="font-size:0.75rem;color:#0083FF;">↑{b['star_gain']:,} ({s_mult}x)</span>
                        </div>
                        <div style="font-size:0.72rem;color:#737368;">was {b['stars_then']:,}</div>
                    </div>
                    <div>
                        <div style="font-size:0.68rem;text-transform:uppercase;letter-spacing:0.07em;color:#737368;">Signal Score</div>
                        <div style="font-weight:700;color:#150F3A;">{b['score_now']}
                            <span style="font-size:0.75rem;color:{delta_color};">{delta_str} pts</span>
                        </div>
                    </div>
                    <div>
                        <div style="font-size:0.68rem;text-transform:uppercase;letter-spacing:0.07em;color:#737368;">Tracked Since</div>
                        <div style="font-weight:700;color:#150F3A;">{b['first_seen']}</div>
                    </div>
                </div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("")
        if st.button("📬 Email breakout list to Brent & Thomas", key="email_breakouts"):
            from notifications import send_breakout_alert
            for email in ["brent@m13.co", "thomas@m13.co"]:
                send_breakout_alert(breakouts, to_email=email)
            audit(USER_EMAIL, "breakout_alert_sent", {"count": len(breakouts), "triggered_by": "manual"})
            st.success(f"Breakout alert sent — {len(breakouts)} candidates.")


# ═══════════════════════════════════════════════════════════════════════════════
# TAB: History
# ═══════════════════════════════════════════════════════════════════════════════
with tab_history:
    st.markdown('<div class="section-header">Your Search History</div>', unsafe_allow_html=True)
    history = get_run_history(USER_EMAIL, limit=25)
    if not history:
        st.caption("No searches run yet.")
    else:
        for run in history:
            ran_at = (run.get("ran_at") or "")[:16].replace("T", " ")
            triggered = run.get("triggered_by", "manual")
            trigger_icon = "🤖" if triggered == "scheduler" else "👤"
            st.markdown(f"""
            <div class="history-row">
                <strong style="color:#150F3A;">{run.get('intent','')}</strong>
                <span style="color:#737368;margin-left:0.75rem;font-size:0.75rem;">
                    {run.get('mode','')} · {trigger_icon} {triggered} · {ran_at} · {run.get('result_count',0)} results
                </span>
            </div>
            """, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════════════════
# TAB: Settings
# ═══════════════════════════════════════════════════════════════════════════════
with tab_settings:
    st.markdown('<div class="section-header">Notification Preferences</div>', unsafe_allow_html=True)

    with st.form("notif_prefs"):
        pref_email = st.text_input(
            "Send notifications to",
            value=prefs.get("notify_email") or USER_EMAIL,
        )
        pref_freq = st.selectbox(
            "Recap frequency",
            ["Weekly", "Bi-Daily", "Daily", "Monthly"],
            index=["Weekly", "Bi-Daily", "Daily", "Monthly"].index(
                prefs.get("recap_frequency", "Weekly")
            ),
        )
        pref_new_match = st.checkbox(
            "Notify me when a saved search finds new candidates",
            value=prefs.get("notify_on_new_match", True),
        )
        save_prefs_btn = st.form_submit_button("Save Preferences", type="primary")
        if save_prefs_btn:
            if not pref_email.endswith("@m13.co"):
                st.warning("Notification email must be an @m13.co address.")
            else:
                ok = save_notification_prefs(
                    user_email=USER_EMAIL,
                    notify_email=pref_email,
                    recap_frequency=pref_freq,
                    notify_on_new_match=pref_new_match,
                    digest_recipients=[],
                )
                if ok:
                    st.success("Preferences saved!")
                    prefs["notify_email"] = pref_email
                    prefs["recap_frequency"] = pref_freq
                    prefs["notify_on_new_match"] = pref_new_match
                else:
                    st.error("Failed to save. Make sure the Supabase schema is up to date.")

    st.markdown("---")
    st.markdown('<div class="section-header">Scheduler</div>', unsafe_allow_html=True)
    st.markdown("""
    <div style="background:white;border-radius:12px;padding:1.25rem 1.5rem;border:1px solid #E8E8EC;font-size:0.85rem;color:#150F3A;">
        <p style="margin:0 0 0.75rem;font-weight:600;">Auto-run saved searches via cron</p>
        <p style="margin:0 0 0.5rem;color:#737368;">
            Add this to your server's crontab to run all saved searches every morning at 8am and notify users of new matches:
        </p>
        <code style="background:#F7F7F8;padding:0.4rem 0.75rem;border-radius:6px;display:block;margin-top:0.5rem;font-size:0.78rem;color:#0083FF;">
            0 8 * * * cd /path/to/github_sourcing && python3 scheduler.py >> logs/scheduler.log 2>&1
        </code>
        <p style="margin:0.75rem 0 0;color:#737368;font-size:0.78rem;">
            The scheduler runs each saved search, finds new candidates vs prior runs, and sends email notifications — fully automated, no manual clicks needed.
        </p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("---")
    st.markdown('<div class="section-header">Account</div>', unsafe_allow_html=True)
    st.markdown(f"""
    <div style="background:white;border-radius:12px;padding:1.25rem 1.5rem;border:1px solid #E8E8EC;font-size:0.85rem;">
        <div style="font-weight:600;color:#150F3A;">{USER_NAME}</div>
        <div style="color:#737368;">{USER_EMAIL}</div>
    </div>
    """, unsafe_allow_html=True)
    st.markdown("")
    if st.button("Sign out", key="signout_settings"):
        audit(USER_EMAIL, "logout")
        logout()
        st.rerun()

st.markdown("")
st.caption(f"Built for M13 · Powered by GitHub API · {datetime.now().strftime('%b %d, %Y')}")
