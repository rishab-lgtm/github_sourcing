"""Application service for normalized, repeatable sourcing execution."""

from __future__ import annotations

from dataclasses import dataclass, field

from github_sourcing import (
    find_sf_ai_contributors,
    find_trending_repo_authors,
    search_by_intent,
)


@dataclass(frozen=True)
class SearchConfig:
    mode: str
    intent: str = ""
    region: str = ""
    max_results: int = 60
    filters: dict = field(default_factory=dict)

    @classmethod
    def from_saved_search(cls, row: dict) -> "SearchConfig":
        filters = row.get("filters") or {}
        return cls(
            mode=row.get("mode") or "Intent Search",
            intent=row.get("intent") or "",
            region=filters.get("region") or "",
            max_results=min(max(int(filters.get("max_results") or 60), 1), 100),
            filters=filters,
        )


def execute_search(config: SearchConfig) -> list[dict]:
    """Execute one search configuration and return normalized candidate matches."""
    if config.mode == "Intent Search":
        if not config.intent.strip():
            raise ValueError("Search intent is required")
        return search_by_intent(
            intent=config.intent,
            location=config.region,
            max_results=config.max_results,
        )
    if config.mode == "SF-Based AI Contributors":
        return find_sf_ai_contributors(limit_per_repo=30)
    if config.mode == "Trending Repo Authors":
        return find_trending_repo_authors(days=7)
    raise ValueError(f"Unsupported search mode: {config.mode}")
