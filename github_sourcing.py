"""
M13 GitHub Sourcing — Core search engine.
Translates user intent into real GitHub discovery queries with keyword expansion,
proper API handling, explainable scoring, and match reasons.
"""

from __future__ import annotations

import os
import re
import time
import json
import csv
import logging
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

load_dotenv()
log = logging.getLogger(__name__)

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

TIMEOUT = 12
MAX_RETRIES = 3
RETRY_BACKOFF = 2

# ── Per-user rate limit tracker ───────────────────────────────────────────────
# Tracks API calls per user email so one user can't exhaust the quota for others.
_user_request_counts: dict[str, int] = {}
SESSION_REQUEST_LIMIT = 4500  # GitHub allows 5000/hr authenticated; leaving small buffer
_current_user: str = ""


def set_current_user(email: str):
    global _current_user
    _current_user = email or ""


def get_session_request_count() -> int:
    return _user_request_counts.get(_current_user, 0)


def session_budget_ok() -> bool:
    return _user_request_counts.get(_current_user, 0) < SESSION_REQUEST_LIMIT


# ── Keyword expansion maps ───────────────────────────────────────────────────
DOMAIN_EXPANSIONS: dict[str, list[str]] = {
    "biotech": [
        "biotech", "biology", "genomics", "computational biology", "bioinformatics",
        "protein", "drug discovery", "therapeutics", "AlphaFold", "wet lab",
        "ml biology", "bio ml", "single cell", "CRISPR", "sequencing",
        "proteomics", "transcriptomics", "cheminformatics", "molecular",
    ],
    "climate": [
        "climate", "clean energy", "renewable", "carbon", "sustainability",
        "grid", "battery", "solar", "wind", "emissions", "net zero",
        "energy storage", "electrification", "climate tech",
    ],
    "fintech": [
        "fintech", "payments", "banking", "lending", "credit", "crypto",
        "defi", "blockchain", "trading", "risk", "compliance", "regtech",
        "neobank", "wealth management", "insurtech",
    ],
    "robotics": [
        "robotics", "robot", "autonomous", "ROS", "manipulation", "SLAM",
        "drone", "embodied", "motion planning", "control systems", "actuator",
        "perception", "sim to real",
    ],
    "security": [
        "security", "cybersecurity", "infosec", "cryptography", "zero trust",
        "vulnerability", "penetration testing", "threat", "SOC", "SIEM",
        "identity", "IAM", "privacy", "encryption",
    ],
    "devtools": [
        "developer tools", "devtools", "SDK", "API", "CLI", "IDE", "compiler",
        "debugger", "observability", "monitoring", "CI/CD", "deployment",
        "infrastructure", "platform engineering", "developer experience",
    ],
    "ai": [
        "machine learning", "deep learning", "LLM", "language model", "neural",
        "transformer", "diffusion", "generative", "inference", "training",
        "RLHF", "fine tuning", "embedding", "RAG", "agent", "multimodal",
    ],
    "healthcare": [
        "healthcare", "health", "medical", "clinical", "EHR", "imaging",
        "radiology", "pathology", "digital health", "telehealth", "pharma",
        "FDA", "HIPAA", "patient", "diagnosis", "treatment",
    ],
    "data": [
        "data engineering", "data pipeline", "ETL", "warehouse", "lakehouse",
        "dbt", "Spark", "Flink", "Kafka", "Airflow", "analytics", "BI",
        "streaming", "real time", "data platform",
    ],
    "hardware": [
        "hardware", "chip", "semiconductor", "FPGA", "ASIC", "PCB",
        "embedded", "firmware", "silicon", "processor", "GPU", "TPU",
        "edge computing", "IoT",
    ],
}

SF_KEYWORDS = [
    "san francisco", "sf", "bay area", "silicon valley", "south bay",
    "palo alto", "menlo park", "mountain view", "sunnyvale", "berkeley",
    "oakland", "san jose", "santa clara", "fremont", "hayward", "san mateo",
    "redwood city", "cupertino", "campbell", "los altos", "los gatos",
    "saratoga", "milpitas", "san leandro", "ca, usa", "california",
]

RESEARCHER_KEYWORDS = [
    "phd", "research", "ml", "ai", "machine learning", "deep learning",
    "scientist", "lab", "university", "prof", "postdoc", "grad student",
]

TOP_LAB_KEYWORDS = [
    "openai", "deepmind", "google brain", "google deepmind", "meta ai", "fair",
    "anthropic", "mistral", "cohere", "xai", "inflection", "stability",
    "hugging face", "nvidia research", "microsoft research", "apple ml",
    "scale ai", "cerebras", "together ai", "replicate", "modal", "anyscale",
    "mosaic", "databricks",
]

FOUNDER_SIGNAL_KEYWORDS = [
    "building", "founder", "co-founder", "stealth", "previously", "ex-",
    "formerly", "alumni", "independent", "open to", "looking for", "startup",
    "llm", "agent", "inference", "infra", "platform", "developer tools",
]

AI_REPOS = [
    "vllm-project/vllm", "ggerganov/llama.cpp", "ollama/ollama",
    "huggingface/transformers", "facebookresearch/llama", "openai/whisper",
    "karpathy/nanoGPT", "microsoft/autogen", "langchain-ai/langchain",
    "openai/openai-python", "ray-project/ray", "modal-labs/modal-client",
    "replicate/cog", "mistralai/mistral-src", "deepseek-ai/DeepSeek-V2",
]

ROLE_TERMS = {
    "ai", "ml", "researcher", "research", "engineer", "developer", "scientist",
    "founder", "phd", "professor", "student", "builder", "expert", "specialist",
}

FILLER_TERMS = {"and", "for", "the", "with", "who", "from", "into", "working"}


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def _get(url: str, params: dict = None, retries: int = None):
    """GET with timeout, retry, rate-limit handling, and per-user budget tracking."""
    if not session_budget_ok():
        log.warning("Session request limit reached (%d). Skipping %s", SESSION_REQUEST_LIMIT, url)
        return None

    max_attempts = retries if retries is not None else MAX_RETRIES
    for attempt in range(max_attempts):
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
            _user_request_counts[_current_user] = _user_request_counts.get(_current_user, 0) + 1
        except requests.exceptions.Timeout:
            log.warning("Timeout on %s (attempt %d/%d)", url, attempt + 1, max_attempts)
            if attempt < max_attempts - 1:
                time.sleep(RETRY_BACKOFF * (attempt + 1))
                continue
            return None
        except (requests.exceptions.RequestException, ConnectionError, OSError) as e:
            log.warning("Request error on %s: %s", url, e)
            return None

        if resp.status_code in (403, 429) and (
            "rate limit" in resp.text.lower() or resp.status_code == 429
        ):
            reset = int(resp.headers.get("X-RateLimit-Reset", time.time() + 60))
            wait = min(max(reset - int(time.time()), 1), 120)
            log.info("Rate limited (HTTP %d) — waiting %ds...", resp.status_code, wait)
            time.sleep(wait)
            continue

        if resp.status_code == 404:
            return None

        if not resp.ok:
            log.warning("HTTP %d on %s: %s", resp.status_code, url, resp.text[:200])
            if attempt < max_attempts - 1:
                time.sleep(RETRY_BACKOFF)
                continue
            return None

        try:
            return resp.json()
        except Exception:
            return None

    return None


def _paginate(url: str, params: dict = None, max_pages: int = 3) -> list:
    """
    Fetch multiple pages from a GitHub endpoint.
    Handles both list responses (contributors, etc.) and search responses ({items: [...]}).
    Stops early when a partial page is returned (no more results).
    """
    params = dict(params or {})
    params.setdefault("per_page", 30)
    results = []
    for page in range(1, max_pages + 1):
        params["page"] = page
        data = _get(url, params)
        if data is None:
            break
        if isinstance(data, list):
            results.extend(data)
            if len(data) < params["per_page"]:
                break
        elif isinstance(data, dict) and "items" in data:
            items = data["items"] or []
            results.extend(items)
            if len(items) < params["per_page"]:
                break
        else:
            break
    return results


# ── Keyword expansion ────────────────────────────────────────────────────────

def expand_query(intent: str) -> list[str]:
    """
    Turn a free-text intent into GitHub search terms.
    Matches known domain expansions + always includes the raw words.
    """
    intent_lower = intent.lower()
    terms: set[str] = set(re.findall(r'\b\w+\b', intent_lower))

    for domain, keywords in DOMAIN_EXPANSIONS.items():
        if domain in intent_lower or any(kw.split()[0] in intent_lower for kw in keywords[:3]):
            terms.update(keywords)

    for word in re.findall(r'\b\w{3,}\b', intent_lower):
        terms.add(word)

    return sorted(terms, key=lambda value: (value.lower(), len(value)))


def triggered_domains(intent: str) -> list[str]:
    """Return known product domains explicitly requested by the user."""
    intent_lower = intent.lower()
    return [domain for domain in DOMAIN_EXPANSIONS if domain in intent_lower]


def required_evidence_terms(intent: str) -> list[str]:
    """
    Terms that must be supported by candidate or source evidence.

    For a cross-domain request such as "biotech AI researcher", biotech is the
    required domain and AI/researcher are role signals. This prevents a popular
    generic AI profile from ranking merely because it has followers or stars.
    """
    domains = [domain for domain in triggered_domains(intent) if domain != "ai"]
    if domains:
        terms: list[str] = []
        for domain in domains:
            terms.extend([domain, *DOMAIN_EXPANSIONS[domain]])
        return list(dict.fromkeys(term.lower() for term in terms))

    raw = [
        word.lower() for word in re.findall(r'\b[\w-]{3,}\b', intent)
        if word.lower() not in FILLER_TERMS
    ]
    return list(dict.fromkeys(raw or [intent.strip().lower()]))


def discovery_terms(intent: str, limit: int = 6) -> list[str]:
    """Pick deterministic, domain-first terms for real GitHub API queries."""
    required = required_evidence_terms(intent)
    preferred = [term for term in required if term in triggered_domains(intent)]
    multiword = [term for term in required if " " in term and term not in preferred]
    single = [term for term in required if " " not in term and term not in preferred]
    ordered = preferred + multiword + single
    return list(dict.fromkeys(ordered))[:limit]


def build_github_user_query(intent: str, location: str = "", min_followers: int = 0) -> str:
    bio_terms = discovery_terms(intent, limit=3)
    bio_q = " OR ".join(f'"{t}"' if " " in t else t for t in bio_terms)
    parts = [bio_q]
    if location:
        parts.append(f'location:"{location}"')
    if min_followers:
        parts.append(f"followers:>={min_followers}")
    return " ".join(parts)


def build_github_repo_query(intent: str, language: str = "", since_days: int = 0) -> str:
    terms = discovery_terms(intent, limit=1)
    query_term = terms[0] if terms else intent.strip()
    quoted = f'"{query_term}"' if " " in query_term else query_term
    parts = [f"{quoted} in:name,description,readme"]
    if language:
        parts.append(f"language:{language}")
    if since_days:
        since = (datetime.now() - timedelta(days=since_days)).strftime("%Y-%m-%d")
        parts.append(f"pushed:>{since}")
    parts.append("stars:>5")
    return " ".join(parts)


def _repo_evidence(repo: dict) -> str:
    topics = " ".join(repo.get("topics") or [])
    return " ".join(filter(None, [
        repo.get("full_name") or repo.get("name") or "",
        repo.get("description") or "",
        topics,
    ]))


def _profile_evidence(profile: dict, source_evidence: str = "") -> str:
    repos = profile.get("top_repos") or []
    if isinstance(repos, list):
        repo_text = " ".join(
            f"{repo.get('name', '')} {repo.get('description', '')} {repo.get('language', '')}"
            for repo in repos if isinstance(repo, dict)
        )
    else:
        repo_text = str(repos)
    return " ".join(filter(None, [
        profile.get("bio") or "",
        profile.get("company") or "",
        profile.get("name") or "",
        repo_text,
        source_evidence,
    ])).lower()


def matched_evidence_terms(intent: str, profile: dict, source_evidence: str = "") -> list[str]:
    evidence = _profile_evidence(profile, source_evidence)
    return [term for term in required_evidence_terms(intent) if term and term in evidence]


def candidate_matches_intent(intent: str, profile: dict, source_evidence: str = "") -> bool:
    """Reject candidates with no concrete evidence for the requested domain."""
    return bool(matched_evidence_terms(intent, profile, source_evidence))


# ── Profile helpers ──────────────────────────────────────────────────────────

def is_sf_based(location: str) -> bool:
    if not location:
        return False
    return any(kw in location.lower() for kw in SF_KEYWORDS)


def get_user_profile(username: str) -> dict:
    data = _get(f"https://api.github.com/users/{username}")
    if not isinstance(data, dict) or not data.get("login"):
        return {}
    return data


def get_user_repos(username: str, limit: int = 5) -> list:
    repos = _get(
        f"https://api.github.com/users/{username}/repos",
        params={"sort": "stars", "per_page": limit},
    )
    if not isinstance(repos, list):
        return []
    return [
        {
            "name": r.get("name", ""),
            "stars": r.get("stargazers_count", 0),
            "language": r.get("language", ""),
            "description": r.get("description") or "",
        }
        for r in repos
        if isinstance(r, dict)
    ]


def _account_age_years(created_at: str):
    if not created_at:
        return None
    try:
        created = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        return round((now - created).days / 365, 1)
    except Exception:
        return None


# ── Scoring ──────────────────────────────────────────────────────────────────

def founder_signal(bio: str, company: str = "") -> dict:
    text = f"{bio or ''} {company or ''}".lower()
    badges, boost = [], 0
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


def compute_signal_score(
    profile: dict,
    search_terms: list[str] = None,
    contributes_to_ai: bool = False,
    is_sf: bool = False,
    source_evidence: str = "",
) -> tuple[int, list[str]]:
    """Score 0–100. Returns (score, reasons).
    top_repos may be a list of dicts (from get_user_repos) or a formatted string
    like "repo-name (1234⭐)" (from stored/cached profiles).
    """
    import re as _re
    score = 0
    reasons: list[str] = []
    bio = (profile.get("bio") or "").lower()
    company = (profile.get("company") or "").lower()
    followers = profile.get("followers", 0) or 0
    top_repos = profile.get("top_repos", [])

    # Normalise top_repos to a star count and text blob regardless of format
    if isinstance(top_repos, str):
        total_stars = sum(int(s) for s in _re.findall(r'\((\d+)⭐\)', top_repos))
        repo_text = top_repos.lower()
    elif isinstance(top_repos, list):
        total_stars = sum(r.get("stars", 0) for r in top_repos if isinstance(r, dict))
        repo_text = " ".join(
            f"{r.get('name','')} {r.get('description','')}"
            for r in top_repos if isinstance(r, dict)
        ).lower()
    else:
        total_stars = 0
        repo_text = ""

    # Also accept contributes_to_ai from the profile dict itself (stored profiles)
    contributes_to_ai = contributes_to_ai or bool(profile.get("contributes_to_ai"))

    if is_sf:
        score += 20
        reasons.append("Based in SF / Bay Area")

    if contributes_to_ai:
        score += 25
        reasons.append("Contributes to major AI repos")

    if search_terms:
        evidence = f"{bio} {repo_text} {company} {source_evidence.lower()}"
        matched = [t for t in search_terms if t.lower() in evidence]
        if matched:
            score += min(len(matched) * 5, 25)
            reasons.append(f"Matches: {', '.join(matched[:4])}")

    if total_stars > 5000:
        score += 20
        reasons.append(f"{total_stars:,} total stars")
    elif total_stars > 1000:
        score += 12
        reasons.append(f"{total_stars:,} total stars")
    elif total_stars > 100:
        score += 6

    if followers > 1000:
        score += 8
        reasons.append(f"{followers:,} followers")
    elif followers > 200:
        score += 4

    if followers < 200 and total_stars > 500:
        score += 10
        reasons.append("Low followers, high stars — hidden gem")

    fs = founder_signal(bio, company)
    score += fs["boost"]
    for badge in fs["badges"]:
        reasons.append(f"Signal: {badge.split(' ', 1)[1]}")

    return min(score, 100), reasons


def format_profile(profile: dict, search_terms: list[str] = None,
                   contributes_to_ai: bool = False, extra: dict = None,
                   source_evidence: str = "") -> dict:
    top_repos = profile.get("top_repos", [])
    bio = profile.get("bio") or ""
    company = profile.get("company") or ""
    location = profile.get("location") or ""
    fs = founder_signal(bio, company)
    is_sf = is_sf_based(location)
    score, reasons = compute_signal_score(
        profile, search_terms=search_terms,
        contributes_to_ai=contributes_to_ai, is_sf=is_sf,
        source_evidence=source_evidence,
    )
    result = {
        "handle": (profile.get("login") or "").lower(),  # always lowercase for dedup consistency
        "name": profile.get("name") or "",
        "location": location,
        "bio": bio,
        "company": company,
        "followers": profile.get("followers", 0),
        "public_repos": profile.get("public_repos", 0),
        "top_repos": ", ".join(
            f"{r['name']}({r['stars']}⭐)" for r in top_repos if isinstance(r, dict)
        ),
        "contributes_to_ai": contributes_to_ai,
        "signal_score": score,
        "match_reasons": reasons,
        "founder_badges": " | ".join(fs["badges"]),
        "account_age_years": _account_age_years(profile.get("created_at", "")),
        "github_url": profile.get("html_url", ""),
    }
    if extra:
        result.update(extra)
    return result


# ── Search engine ────────────────────────────────────────────────────────────

def search_by_intent(intent: str, location: str = "",
                     min_followers: int = 0, max_results: int = 60) -> list[dict]:
    """
    Translate free-text intent into real GitHub discovery.
    Three strategies run sequentially, deduped by handle.
    """
    intent = intent.strip()
    if not intent:
        return []
    expanded = expand_query(intent)
    seen: set[str] = set()
    candidates: list[dict] = []

    # Strategy 1: user bio search — scale pages with max_results
    user_pages = max(3, (max_results // 30) + 1)
    user_q = build_github_user_query(intent, location=location, min_followers=min_followers)
    log.info("User search: %s", user_q)
    user_items = _paginate("https://api.github.com/search/users", params={
        "q": user_q, "sort": "followers", "order": "desc", "per_page": 30,
    }, max_pages=user_pages)
    for item in user_items:
        username = (item.get("login") or "").lower()
        if not username or username in seen:
            continue
        seen.add(username)
        profile = get_user_profile(username)
        if not profile or profile.get("type") == "Organization":
            continue
        profile["top_repos"] = get_user_repos(username)
        if not candidate_matches_intent(intent, profile):
            continue
        candidates.append(format_profile(profile, search_terms=expanded))
        if len(candidates) >= max_results:
            break

    # Strategy 2: domain repo search → owners and contributors. Each domain term
    # drives its own GitHub query so broad intent expansion does not become an
    # impossibly restrictive AND query.
    repo_items: list[dict] = []
    repo_seen: set[str] = set()
    for term in discovery_terms(intent):
        term_intent = term
        repo_q = build_github_repo_query(term_intent)
        log.info("Repo search: %s", repo_q)
        for repo in _paginate("https://api.github.com/search/repositories", params={
            "q": repo_q, "sort": "stars", "order": "desc", "per_page": 10,
        }, max_pages=2):
            repo_full = repo.get("full_name") or ""
            if repo_full and repo_full not in repo_seen:
                repo_seen.add(repo_full)
                repo_items.append(repo)
        if len(repo_items) >= 30:
            break

    for repo in repo_items:
        source_evidence = _repo_evidence(repo)
        if not any(term in source_evidence.lower() for term in required_evidence_terms(intent)):
            continue
        owner = (repo.get("owner", {}).get("login") or "").lower()
        if not owner or owner in seen:
            continue
        seen.add(owner)
        profile = get_user_profile(owner)
        if not profile or profile.get("type") == "Organization":
            continue
        profile["top_repos"] = get_user_repos(owner)
        if not candidate_matches_intent(intent, profile, source_evidence):
            continue
        candidates.append(format_profile(
            profile, search_terms=expanded,
            source_evidence=source_evidence,
            extra={
                "source_repo": repo.get("full_name"),
                "source_repo_stars": repo.get("stargazers_count", 0),
                "match_evidence": source_evidence[:300],
            },
        ))

        # Contributors to a relevant repository are useful even when the owner
        # is an organization. Relevance still comes from the source repository.
        repo_full = repo.get("full_name") or ""
        contributors = _get(
            f"https://api.github.com/repos/{repo_full}/contributors",
            params={"per_page": 5},
        )
        for contributor in (contributors if isinstance(contributors, list) else []):
            username = (contributor.get("login") or "").lower()
            if not username or username in seen:
                continue
            profile = get_user_profile(username)
            if not profile or profile.get("type") == "Organization":
                continue
            profile["top_repos"] = get_user_repos(username)
            if not candidate_matches_intent(intent, profile, source_evidence):
                continue
            seen.add(username)
            candidates.append(format_profile(
                profile,
                search_terms=expanded,
                contributes_to_ai="ai" in intent.lower(),
                source_evidence=source_evidence,
                extra={
                    "source_repo": repo_full,
                    "source_repo_stars": repo.get("stargazers_count", 0),
                    "match_evidence": source_evidence[:300],
                },
            ))

    candidates.sort(key=lambda x: -x["signal_score"])
    return candidates[:max_results]


# ── Legacy scan modes ────────────────────────────────────────────────────────

def find_sf_ai_contributors(limit_per_repo: int = 30) -> list[dict]:
    seen: set[str] = set()
    results: list[dict] = []
    for repo in AI_REPOS:
        contributors = _get(
            f"https://api.github.com/repos/{repo}/contributors",
            params={"per_page": limit_per_repo},
        )
        if not isinstance(contributors, list):
            continue
        for c in contributors:
            username = c.get("login")
            if not username or username in seen:
                continue
            seen.add(username)
            profile = get_user_profile(username)
            if not profile or not is_sf_based(profile.get("location") or ""):
                continue
            profile["top_repos"] = get_user_repos(username)
            results.append(format_profile(
                profile, search_terms=["AI", "machine learning", "LLM"],
                contributes_to_ai=True,
            ))
    results.sort(key=lambda x: -x["signal_score"])
    return results


def find_trending_repo_authors(days: int = 7, language: str = "Python", limit: int = 50) -> list[dict]:
    since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    data = _get("https://api.github.com/search/repositories", params={
        "q": f"language:{language} created:>{since} stars:>50",
        "sort": "stars", "order": "desc", "per_page": limit,
    })
    results: list[dict] = []
    seen: set[str] = set()
    for repo in ((data or {}).get("items") or []):
        owner = repo.get("owner", {}).get("login")
        if not owner or owner in seen:
            continue
        seen.add(owner)
        profile = get_user_profile(owner)
        if not profile or profile.get("type") == "Organization":
            continue
        profile["top_repos"] = get_user_repos(owner)
        results.append(format_profile(
            profile,
            extra={"trending_repo": repo.get("full_name"), "trending_repo_stars": repo.get("stargazers_count")},
        ))
    results.sort(key=lambda x: -x["signal_score"])
    return results


# ── Export ───────────────────────────────────────────────────────────────────

def export_csv(profiles: list, filename: str = "output/results.csv"):
    os.makedirs("output", exist_ok=True)
    if not profiles:
        return
    with open(filename, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=profiles[0].keys())
        writer.writeheader()
        writer.writerows(profiles)


def export_json(profiles: list, filename: str = "output/results.json"):
    os.makedirs("output", exist_ok=True)
    with open(filename, "w") as f:
        json.dump(profiles, f, indent=2)
