"""
M13 GitHub Sourcing Tool
Surfaces high-signal engineers and founders from GitHub before they're on anyone's radar.
"""

import os
import time
import json
import csv
import requests
from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

SF_KEYWORDS = ["san francisco", "sf", "bay area", "silicon valley", "south bay", "palo alto", "menlo park", "mountain view", "sunnyvale", "berkeley", "oakland", "san jose", "santa clara", "fremont", "hayward", "san mateo", "redwood", "cupertino", "campbell", "los altos", "los gatos", "saratoga", "milpitas", "san leandro", "ca, usa", "california"]
RESEARCHER_KEYWORDS = ["phd", "research", "ml", "ai", "machine learning", "deep learning", "scientist", "lab", "university", "prof"]
AI_REPOS = [
    # LLM inference & serving
    "vllm-project/vllm", "ggerganov/llama.cpp", "ollama/ollama", "lm-sys/FastChat",
    # Foundational models & training
    "huggingface/transformers", "facebookresearch/llama", "openai/whisper", "karpathy/nanoGPT",
    # Agents & tooling
    "microsoft/autogen", "langchain-ai/langchain", "openai/openai-python",
    # Infra & platforms
    "ray-project/ray", "modal-labs/modal-client", "replicate/cog",
    # Hot recent projects
    "mistralai/mistral-src", "anthropics/anthropic-sdk-python", "deepseek-ai/DeepSeek-V2",
]

# Signals that suggest someone could be a future founder
FOUNDER_SIGNAL_KEYWORDS = [
    "building", "founder", "co-founder", "stealth", "prev", "previously", "ex-",
    "formerly", "alumni", "alum", "independent", "open to", "looking for",
    "startup", "llm", "agent", "inference", "infra", "platform", "developer tools",
]
TOP_LAB_KEYWORDS = [
    "openai", "deepmind", "google brain", "google deepmind", "meta ai", "fair",
    "anthropic", "mistral", "cohere", "xai", "inflection", "stability", "hugging face",
    "nvidia research", "microsoft research", "apple ml", "scale ai", "cerebras",
    "together ai", "replicate", "modal", "anyscale", "mosaic", "databricks",
]


def _get(url, params=None):
    """Make a GitHub API GET request with basic rate limit handling."""
    resp = requests.get(url, headers=HEADERS, params=params)
    if resp.status_code == 403 and "rate limit" in resp.text.lower():
        reset = int(resp.headers.get("X-RateLimit-Reset", time.time() + 60))
        wait = max(reset - int(time.time()), 1)
        print(f"  Rate limited — waiting {wait}s...")
        time.sleep(wait)
        resp = requests.get(url, headers=HEADERS, params=params)
    resp.raise_for_status()
    return resp.json()


def is_sf_based(location: str) -> bool:
    if not location:
        return False
    return any(kw in location.lower() for kw in SF_KEYWORDS)


def is_researcher(bio: str, location: str = "") -> bool:
    text = f"{bio or ''} {location or ''}".lower()
    return any(kw in text for kw in RESEARCHER_KEYWORDS)


def founder_signal(bio: str, company: str = "") -> dict:
    """
    Detect founder-like signals from bio and company.
    Returns {score_boost, badges} where badges are short labels shown in the UI.
    """
    text = f"{bio or ''} {company or ''}".lower()
    badges = []
    boost = 0

    if any(kw in text for kw in TOP_LAB_KEYWORDS):
        badges.append("🏛 Top Lab")
        boost += 20

    if any(kw in text for kw in FOUNDER_SIGNAL_KEYWORDS):
        badges.append("🚀 Building")
        boost += 15

    if any(kw in text for kw in RESEARCHER_KEYWORDS):
        badges.append("🔬 Researcher")
        boost += 10

    return {"boost": boost, "badges": badges}


def get_user_profile(username: str) -> dict:
    """Fetch full profile for a GitHub user."""
    try:
        return _get(f"https://api.github.com/users/{username}")
    except Exception:
        return {}


def get_user_repos(username: str, limit: int = 5) -> list:
    """Fetch top repos for a user sorted by stars."""
    try:
        repos = _get(f"https://api.github.com/users/{username}/repos", params={"sort": "stars", "per_page": limit})
        return [{"name": r["name"], "stars": r["stargazers_count"], "language": r["language"], "description": r["description"]} for r in repos]
    except Exception:
        return []


def compute_signal_score(profile: dict, filters: dict) -> int:
    """Score a profile 0-100 based on active filters."""
    score = 0
    bio = profile.get("bio") or ""
    location = profile.get("location") or ""
    company = profile.get("company") or ""
    followers = profile.get("followers", 0)
    top_repos = profile.get("top_repos", [])
    total_stars = sum(r["stars"] for r in top_repos)

    if filters.get("sf") and is_sf_based(location):
        score += 30
    if filters.get("researcher") and is_researcher(bio, location):
        score += 25
    if filters.get("ai_contributor") and profile.get("contributes_to_ai"):
        score += 25
    if total_stars > 1000:
        score += 15
    elif total_stars > 100:
        score += 8
    if followers > 500:
        score += 5
    if followers < 200 and total_stars > 500:
        score += 10

    # founder signal boost
    fs = founder_signal(bio, company)
    score += fs["boost"]

    return min(score, 100)


def enrich_profile(username: str, filters: dict, contributes_to_ai: bool = False) -> dict:
    """Fetch and enrich a GitHub user profile."""
    profile = get_user_profile(username)
    if not profile:
        return {}

    top_repos = get_user_repos(username)
    profile["top_repos"] = top_repos
    profile["contributes_to_ai"] = contributes_to_ai
    profile["signal_score"] = compute_signal_score(profile, filters)
    return profile


def format_profile(profile: dict) -> dict:
    """Flatten a profile into a clean output dict."""
    top_repos = profile.get("top_repos", [])
    bio = profile.get("bio") or ""
    company = profile.get("company") or ""
    fs = founder_signal(bio, company)

    created_at = profile.get("created_at", "")
    account_age_years = None
    if created_at:
        try:
            created = pd.to_datetime(created_at)
            account_age_years = round((pd.Timestamp.now(tz='UTC') - created).days / 365, 1)
        except Exception:
            pass

    return {
        "handle": profile.get("login", ""),
        "name": profile.get("name", ""),
        "location": profile.get("location", "") or "",
        "bio": bio,
        "company": company,
        "followers": profile.get("followers", 0),
        "public_repos": profile.get("public_repos", 0),
        "top_repos": ", ".join(f"{r['name']}({r['stars']}⭐)" for r in top_repos),
        "contributes_to_ai": profile.get("contributes_to_ai", False),
        "signal_score": profile.get("signal_score", 0),
        "founder_badges": " ".join(fs["badges"]),
        "account_age_years": account_age_years,
        "github_url": profile.get("html_url", ""),
    }


# --- Filters ---

def find_sf_ai_contributors(limit_per_repo: int = 30) -> list:
    """
    Use case #1: SF-based engineers actively contributing to open source AI projects.
    """
    print("Scanning AI repos for SF-based contributors...")
    seen = set()
    results = []

    for repo in AI_REPOS:
        print(f"  Checking contributors to {repo}...")
        try:
            contributors = _get(f"https://api.github.com/repos/{repo}/contributors", params={"per_page": limit_per_repo})
        except Exception as e:
            print(f"  Skipping {repo}: {e}")
            continue

        for contributor in contributors:
            username = contributor.get("login")
            if not username or username in seen:
                continue
            seen.add(username)

            profile = get_user_profile(username)
            if not profile:
                continue

            location = profile.get("location") or ""
            if not is_sf_based(location):
                continue

            top_repos = get_user_repos(username)
            profile["top_repos"] = top_repos
            profile["contributes_to_ai"] = True
            filters = {"sf": True, "ai_contributor": True}
            profile["signal_score"] = compute_signal_score(profile, filters)
            results.append(format_profile(profile))
            print(f"    Found: {username} ({location}) — score {profile['signal_score']}")

    results.sort(key=lambda x: -x["signal_score"])
    return results


def find_trending_repo_authors(days: int = 7, language: str = "Python", limit: int = 50) -> list:
    """
    Find authors of trending repos (repos gaining stars fast recently).
    Uses GitHub search sorted by recently updated + stars.
    """
    print(f"Finding trending {language} repos from last {days} days...")
    from datetime import datetime, timedelta
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    try:
        data = _get("https://api.github.com/search/repositories", params={
            "q": f"language:{language} created:>{since} stars:>50",
            "sort": "stars",
            "order": "desc",
            "per_page": limit,
        })
    except Exception as e:
        print(f"Search failed: {e}")
        return []

    results = []
    seen = set()
    for repo in data.get("items", []):
        owner = repo.get("owner", {}).get("login")
        if not owner or owner in seen:
            continue
        seen.add(owner)

        profile = get_user_profile(owner)
        if not profile or profile.get("type") == "Organization":
            continue

        top_repos = get_user_repos(owner)
        profile["top_repos"] = top_repos
        profile["contributes_to_ai"] = False
        filters = {"sf": True}
        profile["signal_score"] = compute_signal_score(profile, filters)
        result = format_profile(profile)
        result["trending_repo"] = repo.get("full_name")
        result["trending_repo_stars"] = repo.get("stargazers_count")
        results.append(result)
        print(f"  Found: {owner} — {repo['full_name']} ({repo['stargazers_count']}⭐)")

    results.sort(key=lambda x: -x["signal_score"])
    return results


# --- Export ---

def export_csv(profiles: list, filename: str = "output/results.csv"):
    os.makedirs("output", exist_ok=True)
    if not profiles:
        print("No profiles to export.")
        return
    keys = profiles[0].keys()
    with open(filename, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=keys)
        writer.writeheader()
        writer.writerows(profiles)
    print(f"Exported {len(profiles)} profiles to {filename}")


def export_json(profiles: list, filename: str = "output/results.json"):
    os.makedirs("output", exist_ok=True)
    with open(filename, "w") as f:
        json.dump(profiles, f, indent=2)
    print(f"Exported {len(profiles)} profiles to {filename}")


# --- Main ---

if __name__ == "__main__":
    print("M13 GitHub Sourcing Tool")
    print("=" * 40)
    print("1. SF-based AI contributors (Use case #1)")
    print("2. Trending repo authors")
    choice = input("\nSelect filter (1 or 2): ").strip()

    if choice == "1":
        results = find_sf_ai_contributors(limit_per_repo=30)
    elif choice == "2":
        results = find_trending_repo_authors(days=7)
    else:
        print("Invalid choice.")
        exit()

    print(f"\nFound {len(results)} profiles.")
    if results:
        fmt = input("Export as (csv/json/both): ").strip().lower()
        if fmt in ("csv", "both"):
            export_csv(results)
        if fmt in ("json", "both"):
            export_json(results)
        print("\nTop 5 by signal score:")
        for p in results[:5]:
            print(f"  {p['handle']} ({p['location']}) — score {p['signal_score']} — {p['github_url']}")
