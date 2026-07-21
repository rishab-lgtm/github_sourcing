from unittest.mock import patch

import pytest

from search_service import SearchConfig, execute_search


def test_saved_search_config_round_trip():
    config = SearchConfig.from_saved_search({
        "mode": "Intent Search",
        "intent": "biotech AI researcher",
        "filters": {"region": "SF", "max_results": 25},
    })
    assert config.intent == "biotech AI researcher"
    assert config.region == "SF"
    assert config.max_results == 25


def test_execute_intent_search_passes_config_to_engine():
    with patch("search_service.search_by_intent", return_value=[{"handle": "bio"}]) as search:
        results = execute_search(SearchConfig(
            mode="Intent Search", intent="biotech", region="SF", max_results=12,
        ))
    assert results == [{"handle": "bio"}]
    search.assert_called_once_with(intent="biotech", location="SF", max_results=12)


def test_empty_intent_fails_before_api_call():
    with pytest.raises(ValueError):
        execute_search(SearchConfig(mode="Intent Search", intent=""))
