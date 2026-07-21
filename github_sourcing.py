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
import threading
from contextvars import ContextVar
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
# ContextVar keeps the active user isolated across Streamlit's concurrent session
# threads. Counters are protected by a lock and reset every hour to match GitHub's
# authenticated API rate-limit window.
_current_user: ContextVar[str] = ContextVar("github_sourcing_user", default="anonymous")
_user_request_windows: dict[str, tuple[float, int]] = {}
_usage_lock = threading.Lock()
_REQUEST_WINDOW_SECONDS = 60 * 60
SESSION_REQUEST_LIMIT = 4500  # GitHub allows 5000/hr authenticated; leaving small buffer


def set_current_user(email: str):
    """Bind API usage to the current Streamlit session or scheduler job."""
    return _current_user.set((email or "anonymous").strip().lower())


def _usage_key() -> str:
    return _current_user.get()


def _request_count(user: str, now=None) -> int:
    now = time.time() if now is None else now
    with _usage_lock:
        started_at, count = _user_request_windows.get(user, (now, 0))
        if now - started_at >= _REQUEST_WINDOW_SECONDS:
            _user_request_windows[user] = (now, 0)
            return 0
        return count


def _consume_request_budget() -> bool:
    """Atomically reserve one request from the current user's hourly budget."""
    user = _usage_key()
    now = time.time()
    with _usage_lock:
        started_at, count = _user_request_windows.get(user, (now, 0))
        if now - started_at >= _REQUEST_WINDOW_SECONDS:
            started_at, count = now, 0
        if count >= SESSION_REQUEST_LIMIT:
            _user_request_windows[user] = (started_at, count)
            return False
        _user_request_windows[user] = (started_at, count + 1)
        return True


def get_session_request_count() -> int:
    return _request_count(_usage_key())


def session_budget_ok() -> bool:
    return get_session_request_count() < SESSION_REQUEST_LIMIT


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
    "founder", "co-founder", "stealth", "yc", "y combinator",
    "seed", "pre-seed", "raising", "independent", "solo", "side project",
    "left", "quit", "departed", "just started", "new company",
]

STRONG_FOUNDER_KEYWORDS = [
    "founder", "co-founder", "stealth", "yc", "y combinator",
    "seed", "pre-seed", "raising", "just launched", "new startup",
    "left google", "left meta", "left openai", "left deepmind",
    "left anthropic", "ex-openai", "ex-google", "ex-meta", "ex-anthropic",
    "ex-deepmind", "ex-nvidia", "started a company", "independent researcher",
    "building in stealth",
]

# Large orgs where "building" does NOT mean startup
BIG_COMPANY_KEYWORDS = [
    "google", "deepmind", "meta", "microsoft", "amazon", "apple", "nvidia",
    "toyota", "samsung", "ibm", "intel", "salesforce", "oracle", "bytedance",
    "tencent", "baidu", "alibaba", "openai", "anthropic", "gemini",
    "waymo", "tesla", "uber", "lyft", "airbnb", "stripe", "palantir",
    "two sigma", "jane street", "citadel", "d.e. shaw", "renaissance",
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

# Functional signals are separate from industry/domain signals. A request for a
# "robotics deployment engineer" should require evidence of both robotics and
# real-world deployment—not merely return every popular roboticist on GitHub.
CAPABILITY_EXPANSIONS: dict[str, dict[str, list[str]]] = {
    "deployment": {
        "triggers": [
            "deployment", "deploy", "deployed", "field engineer",
            "forward deployed", "integration engineer", "implementation engineer",
            "deployment engineer", "field robotics",
        ],
        "terms": [
            "deployment", "deployed", "deploying", "production", "field robotics",
            "field testing", "fielded", "robot fleet", "fleet management",
            "systems integration", "system integration", "commissioning",
            "hardware software integration", "robot bringup", "bring-up",
            "customer site", "on-site", "reliability", "operations",
        ],
    },
    "research": {
        "triggers": ["researcher", "research scientist", "research engineer", "scientist"],
        "terms": [
            "research", "researcher", "scientist", "phd", "postdoc", "publication",
            "laboratory", "lab", "university",
        ],
    },
}

CAPABILITY_DISCOVERY_TERMS = {
    "deployment": [
        "deployment", "field testing", "fleet management",
        "systems integration", "commissioning",
    ],
    "research": ["research", "researcher", "research engineer", "scientist", "phd"],
}


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def _get(url: str, params: dict = None, retries: int = None):
    """GET with timeout, retry, rate-limit handling, and per-user budget tracking."""
    max_attempts = retries if retries is not None else MAX_RETRIES
    for attempt in range(max_attempts):
        if not _consume_request_budget():
            log.warning("Session request limit reached (%d). Skipping %s", SESSION_REQUEST_LIMIT, url)
            return None
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
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

    for capability in triggered_capabilities(intent):
        terms.update(CAPABILITY_EXPANSIONS[capability]["terms"])

    for word in re.findall(r'\b\w{3,}\b', intent_lower):
        terms.add(word)

    return sorted(terms, key=lambda value: (value.lower(), len(value)))


def triggered_domains(intent: str) -> list[str]:
    """Return known product domains explicitly requested by the user."""
    intent_lower = intent.lower()
    return [domain for domain in DOMAIN_EXPANSIONS if domain in intent_lower]


def triggered_capabilities(intent: str) -> list[str]:
    """Return explicitly requested work functions such as deployment or research."""
    intent_lower = intent.lower()
    return [
        capability
        for capability, config in CAPABILITY_EXPANSIONS.items()
        if any(trigger in intent_lower for trigger in config["triggers"])
    ]


def intent_evidence_groups(intent: str) -> dict[str, list[str]]:
    """Build independently required evidence groups for a recruiting intent."""
    groups: dict[str, list[str]] = {}
    domains = triggered_domains(intent)
    non_ai_domains = [domain for domain in domains if domain != "ai"]
    for domain in non_ai_domains or domains:
        groups[f"domain:{domain}"] = list(dict.fromkeys(
            [domain, *DOMAIN_EXPANSIONS[domain]]
        ))
    for capability in triggered_capabilities(intent):
        groups[f"capability:{capability}"] = CAPABILITY_EXPANSIONS[capability]["terms"]
    return groups


def required_evidence_terms(intent: str) -> list[str]:
    """
    Terms that must be supported by candidate or source evidence.

    For a cross-domain request such as "biotech AI researcher", biotech is the
    required domain and AI/researcher are role signals. This prevents a popular
    generic AI profile from ranking merely because it has followers or stars.
    """
    groups = intent_evidence_groups(intent)
    if groups:
        return list(dict.fromkeys(
            term.lower() for terms in groups.values() for term in terms
        ))

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


def discovery_pairs(intent: str, limit: int = 8) -> list[tuple[str, ...]]:
    """Create domain/function combinations for targeted GitHub searches."""
    groups = intent_evidence_groups(intent)
    domain_terms: list[str] = []
    capability_terms: list[str] = []
    for label, terms in groups.items():
        if label.startswith("domain:"):
            domain_terms.extend(terms)
        else:
            capability = label.split(":", 1)[1]
            capability_terms.extend(CAPABILITY_DISCOVERY_TERMS.get(capability, terms))
    domain_terms = list(dict.fromkeys(domain_terms))[:4]
    capability_terms = list(dict.fromkeys(capability_terms))[:5]
    if domain_terms and capability_terms:
        pairs = []
        for offset in range(max(len(domain_terms), len(capability_terms))):
            for index, domain in enumerate(domain_terms):
                capability = capability_terms[(index + offset) % len(capability_terms)]
                pair = (domain, capability)
                if pair not in pairs:
                    pairs.append(pair)
                if len(pairs) >= limit:
                    return pairs
        return pairs
    terms = discovery_terms(intent, limit=limit)
    return [(term,) for term in terms]


def _quote_search_term(term: str) -> str:
    return f'"{term}"' if " " in term else term


def build_github_user_queries(intent: str, location: str = "",
                              min_followers: int = 0, limit: int = 8) -> list[str]:
    queries = []
    for pair in discovery_pairs(intent, limit=limit):
        parts = [" ".join(_quote_search_term(term) for term in pair)]
        if location:
            parts.append(f'location:"{location}"')
        if min_followers:
            parts.append(f"followers:>={min_followers}")
        queries.append(" ".join(parts))
    return list(dict.fromkeys(queries))


def build_github_user_query(intent: str, location: str = "", min_followers: int = 0) -> str:
    queries = build_github_user_queries(intent, location, min_followers, limit=1)
    return queries[0] if queries else intent.strip()


def build_github_repo_query(intent: str, language: str = "", since_days: int = 0) -> str:
    pair = discovery_pairs(intent, limit=1)
    query_terms = pair[0] if pair else (intent.strip(),)
    query_text = " ".join(_quote_search_term(term) for term in query_terms)
    parts = [f"{query_text} in:name,description,readme"]
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


def matched_intent_dimensions(intent: str, profile: dict,
                              source_evidence: str = "") -> dict[str, list[str]]:
    """Return matching terms for every independently required intent dimension."""
    evidence = _profile_evidence(profile, source_evidence)
    return {
        label: [term for term in terms if term and term.lower() in evidence]
        for label, terms in intent_evidence_groups(intent).items()
    }


def candidate_matches_intent(intent: str, profile: dict, source_evidence: str = "") -> bool:
    """Require concrete evidence for every requested domain and work function."""
    dimensions = matched_intent_dimensions(intent, profile, source_evidence)
    if dimensions:
        return all(matches for matches in dimensions.values())
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
    at_big_company = any(kw in text for kw in BIG_COMPANY_KEYWORDS)

    if any(kw in text for kw in STRONG_FOUNDER_KEYWORDS):
        badges.append("🚀 Founder")
        boost += 25
    elif any(kw in text for kw in FOUNDER_SIGNAL_KEYWORDS) and not at_big_company:
        badges.append("🚀 Building")
        boost += 15

    if any(kw in text for kw in TOP_LAB_KEYWORDS):
        if at_big_company and not any(kw in text for kw in STRONG_FOUNDER_KEYWORDS):
            badges.append("🏛 Top Lab")
            boost += 5  # still at big company, not a startup signal
        else:
            badges.append("🏛 Ex-Top Lab")
            boost += 20  # left the lab — much more interesting

    if any(kw in text for kw in RESEARCHER_KEYWORDS):
        badges.append("🔬 Researcher")
        boost += 8
    return {"boost": boost, "badges": badges}


def compute_signal_score(
    profile: dict,
    search_terms: list[str] = None,
    evidence_groups: dict[str, list[str]] = None,
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

    text = f"{bio} {company}"
    at_big_company = any(kw in text for kw in BIG_COMPANY_KEYWORDS)

    # ── Strongest signal: explicit founder/startup intent ─────────────────────
    strong_founder = any(kw in text for kw in STRONG_FOUNDER_KEYWORDS)
    if strong_founder:
        matched_kw = next((kw for kw in STRONG_FOUNDER_KEYWORDS if kw in text), "")
        score += 35
        reasons.append(f"Startup signal: '{matched_kw}'")
    elif not at_big_company and any(kw in text for kw in ["building", "working on", "shipping", "launched"]):
        # "building" only counts as startup signal outside big companies
        score += 15
        reasons.append("Actively building something")
    else:
        # Left a top lab — pre-founder signal
        left_lab = any(kw in text for kw in ["ex-", "formerly", "previously", "left ", "alumni"]) and \
                   any(lab in text for lab in TOP_LAB_KEYWORDS)
        if left_lab:
            score += 20
            reasons.append("Ex-top lab — potential founder")

    # ── Location (nice to have, not dominant) ─────────────────────────────────
    if is_sf:
        score += 5
        reasons.append("Based in SF / Bay Area")

    # ── AI contributions (signal, not the whole story) ────────────────────────
    if contributes_to_ai:
        score += 10
        reasons.append("Contributes to major AI repos")

    evidence = f"{bio} {repo_text} {company} {source_evidence.lower()}"
    if evidence_groups:
        for label, terms in evidence_groups.items():
            matched = [term for term in terms if term.lower() in evidence]
            if matched:
                score += 15
                readable = label.split(":", 1)[-1].replace("_", " ").title()
                reasons.append(f"{readable} evidence: {', '.join(matched[:3])}")
    elif search_terms:
        matched = [t for t in search_terms if t.lower() in evidence]
        if matched:
            score += min(len(matched) * 5, 25)
            reasons.append(f"Matches: {', '.join(matched[:4])}")

    if total_stars > 5000:
        score += 15
        reasons.append(f"{total_stars:,} total stars")
    elif total_stars > 1000:
        score += 10
        reasons.append(f"{total_stars:,} total stars")
    elif total_stars > 200:
        score += 5
        reasons.append(f"{total_stars:,} total stars")

    # Hidden gem: quietly shipping without a big following
    if followers < 500 and total_stars > 500:
        score += 12
        reasons.append("Low followers, high stars — quietly shipping")
    elif followers < 200 and total_stars > 200:
        score += 8
        reasons.append("Low followers, high stars — hidden gem")

    # Followers are a weak signal — big researchers have them too
    if followers > 5000:
        score += 5
        reasons.append(f"{followers:,} followers")
    elif followers > 1000:
        score += 2

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
                   source_evidence: str = "", intent: str = "") -> dict:
    top_repos = profile.get("top_repos", [])
    bio = profile.get("bio") or ""
    company = profile.get("company") or ""
    location = profile.get("location") or ""
    fs = founder_signal(bio, company)
    is_sf = is_sf_based(location)
    score, reasons = compute_signal_score(
        profile, search_terms=search_terms,
        evidence_groups=intent_evidence_groups(intent) if intent else None,
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
    accepted: set[str] = set()
    profile_cache: dict[str, dict] = {}
    candidates: list[dict] = []

    def load_profile(username: str) -> dict:
        username = username.lower()
        if username not in profile_cache:
            profile = get_user_profile(username)
            if profile and profile.get("type") != "Organization":
                profile["top_repos"] = get_user_repos(username)
                profile_cache[username] = profile
            else:
                profile_cache[username] = {}
        return profile_cache[username]

    def add_candidate(username: str, source_evidence: str = "",
                      contributes_to_ai: bool = False, extra: dict = None) -> bool:
        username = (username or "").lower()
        if not username or username in accepted:
            return False
        profile = load_profile(username)
        if not profile or not candidate_matches_intent(intent, profile, source_evidence):
            return False
        candidates.append(format_profile(
            profile,
            search_terms=expanded,
            intent=intent,
            contributes_to_ai=contributes_to_ai,
            source_evidence=source_evidence,
            extra=extra,
        ))
        accepted.add(username)
        return True

    # Strategy 1: targeted user searches. Cross-domain intents use several
    # domain/function pairs instead of one broad OR query.
    user_pages = min(max(1, (max_results // 60) + 1), 3)
    inspected_limit = min(max(max_results * 3, 60), 180)
    for user_q in build_github_user_queries(
        intent, location=location, min_followers=min_followers
    ):
        log.info("User search: %s", user_q)
        user_items = _paginate("https://api.github.com/search/users", params={
            "q": user_q, "sort": "followers", "order": "desc", "per_page": 30,
        }, max_pages=user_pages)
        for item in user_items:
            username = (item.get("login") or "").lower()
            if username in profile_cache:
                continue
            add_candidate(username)
            if len(candidates) >= max_results or len(profile_cache) >= inspected_limit:
                break
        if len(candidates) >= max_results or len(profile_cache) >= inspected_limit:
            break

    # Strategy 2: domain repo search → owners and contributors. Each domain term
    # drives its own GitHub query so broad intent expansion does not become an
    # impossibly restrictive AND query.
    repo_items: list[dict] = []
    repo_seen: set[str] = set()
    for pair in discovery_pairs(intent):
        pair_intent = " ".join(pair)
        pair_query = " ".join(_quote_search_term(term) for term in pair)
        repo_q = f"{pair_query} in:name,description,readme stars:>5"
        log.info("Repo search: %s", repo_q)
        for repo in _paginate("https://api.github.com/search/repositories", params={
            "q": repo_q, "sort": "stars", "order": "desc", "per_page": 10,
        }, max_pages=2):
            repo_full = repo.get("full_name") or ""
            if repo_full and repo_full not in repo_seen:
                repo_seen.add(repo_full)
                sourced_repo = dict(repo)
                sourced_repo["_sourcing_query"] = pair_intent
                repo_items.append(sourced_repo)
        if len(repo_items) >= 30:
            break

    for repo in repo_items:
        source_evidence = f"{_repo_evidence(repo)} {repo.get('_sourcing_query', '')}".strip()
        source_stub = {"bio": "", "company": "", "name": "", "top_repos": []}
        source_dimensions = matched_intent_dimensions(intent, source_stub, source_evidence)
        if source_dimensions and not all(source_dimensions.values()):
            continue
        owner = (repo.get("owner", {}).get("login") or "").lower()
        source_extra = {
                "source_repo": repo.get("full_name"),
                "source_repo_stars": repo.get("stargazers_count", 0),
                "match_evidence": source_evidence[:300],
        }
        add_candidate(owner, source_evidence=source_evidence, extra=source_extra)

        # Contributors to a relevant repository are useful even when the owner
        # is an organization. Relevance comes from the targeted source repository.
        repo_full = repo.get("full_name") or ""
        contributors = _get(
            f"https://api.github.com/repos/{repo_full}/contributors",
            params={"per_page": 5},
        )
        for contributor in (contributors if isinstance(contributors, list) else []):
            username = (contributor.get("login") or "").lower()
            add_candidate(
                username,
                contributes_to_ai="ai" in intent.lower(),
                source_evidence=source_evidence,
                extra=source_extra,
            )
            if len(candidates) >= max_results:
                break
        if len(candidates) >= max_results:
            break

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
