"""
Tests for places.py — Google Places API integration.

Run:
    pytest test_places.py -v
"""
import json
import pytest
from unittest.mock import patch, MagicMock

from places import fetch_place_data, _find_place_id, _fetch_place_details


def _mock_urlopen(payload: dict):
    """Build a context-manager mock for urllib.request.urlopen."""
    mock = MagicMock()
    mock.read.return_value = json.dumps(payload).encode()
    mock.__enter__ = lambda s: mock
    mock.__exit__ = MagicMock(return_value=False)
    return mock


# ─── fetch_place_data — top-level orchestration ──────────────────────────────

class TestFetchPlaceData:

    def test_returns_empty_when_no_api_key(self):
        with patch.dict("os.environ", {"GOOGLE_PLACES_API_KEY": ""}):
            result = fetch_place_data("Test Bistro", "San Diego")
        assert result == {}

    def test_returns_empty_when_business_name_blank(self):
        with patch.dict("os.environ", {"GOOGLE_PLACES_API_KEY": "fake"}):
            result = fetch_place_data("", "San Diego")
        assert result == {}

    def test_full_flow_returns_reviews(self):
        find_payload = {"candidates": [{"place_id": "PLACE_123"}]}
        details_payload = {
            "result": {
                "name": "Test Bistro",
                "rating": 4.6,
                "user_ratings_total": 250,
                "formatted_address": "123 Main St, San Diego, CA",
                "formatted_phone_number": "(619) 555-0000",
                "reviews": [
                    {"author_name": "Jane D.", "rating": 5, "text": "Amazing food!", "relative_time_description": "2 weeks ago"},
                    {"author_name": "Bob S.", "rating": 4, "text": "Great service.", "relative_time_description": "a month ago"},
                ],
            }
        }
        with patch.dict("os.environ", {"GOOGLE_PLACES_API_KEY": "fake"}), \
             patch("urllib.request.urlopen", side_effect=[_mock_urlopen(find_payload), _mock_urlopen(details_payload)]):
            result = fetch_place_data("Test Bistro", "San Diego")
        assert result["place_id"] == "PLACE_123"
        assert result["rating"] == 4.6
        assert result["total_ratings"] == 250
        assert result["address"] == "123 Main St, San Diego, CA"
        assert result["phone"] == "(619) 555-0000"
        assert len(result["reviews"]) == 2
        assert result["reviews"][0]["author"] == "Jane D."
        assert result["reviews"][0]["rating"] == 5
        assert result["reviews"][0]["text"] == "Amazing food!"

    def test_returns_empty_when_no_candidates_found(self):
        find_payload = {"candidates": []}
        with patch.dict("os.environ", {"GOOGLE_PLACES_API_KEY": "fake"}), \
             patch("urllib.request.urlopen", return_value=_mock_urlopen(find_payload)):
            result = fetch_place_data("Nonexistent Biz", "Nowhere")
        assert result == {}

    def test_network_error_returns_empty(self):
        with patch.dict("os.environ", {"GOOGLE_PLACES_API_KEY": "fake"}), \
             patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            result = fetch_place_data("Test Bistro", "San Diego")
        assert result == {}


# ─── _find_place_id ──────────────────────────────────────────────────────────

class TestFindPlaceId:

    def test_returns_first_candidate_place_id(self):
        payload = {"candidates": [{"place_id": "ABC"}, {"place_id": "DEF"}]}
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
            result = _find_place_id("Test", "SD", "fake-key")
        assert result == "ABC"

    def test_returns_empty_when_no_candidates(self):
        payload = {"candidates": []}
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
            result = _find_place_id("Test", "SD", "fake-key")
        assert result == ""

    def test_url_encodes_business_name_with_spaces(self):
        payload = {"candidates": [{"place_id": "X"}]}
        captured = {}
        def capture(req, **kw):
            captured["url"] = req if isinstance(req, str) else req.full_url
            return _mock_urlopen(payload)
        with patch("urllib.request.urlopen", side_effect=capture):
            _find_place_id("The Little Door", "Los Angeles", "fake-key")
        # spaces should be encoded as + or %20 — anything but raw " "
        assert " " not in captured["url"]

    def test_network_error_returns_empty_string(self):
        with patch("urllib.request.urlopen", side_effect=Exception("DNS fail")):
            result = _find_place_id("Test", "SD", "fake-key")
        assert result == ""


# ─── _fetch_place_details ────────────────────────────────────────────────────

class TestFetchPlaceDetails:

    def test_returns_full_dict_with_reviews(self):
        payload = {
            "result": {
                "name": "Test",
                "rating": 4.5,
                "user_ratings_total": 100,
                "reviews": [
                    {"author_name": "A", "rating": 5, "text": "Great", "relative_time_description": "today"},
                ],
            }
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
            result = _fetch_place_details("PID", "fake-key")
        assert result["place_id"] == "PID"
        assert result["rating"] == 4.5
        assert result["total_ratings"] == 100
        assert len(result["reviews"]) == 1

    def test_caps_reviews_at_5(self):
        many_reviews = [
            {"author_name": f"User{i}", "rating": 5, "text": f"Review {i}"}
            for i in range(10)
        ]
        payload = {"result": {"reviews": many_reviews}}
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
            result = _fetch_place_details("PID", "fake-key")
        assert len(result["reviews"]) == 5

    def test_filters_out_reviews_with_no_text(self):
        payload = {
            "result": {
                "reviews": [
                    {"author_name": "A", "rating": 5, "text": "Has text"},
                    {"author_name": "B", "rating": 4, "text": ""},
                    {"author_name": "C", "rating": 5, "text": "   "},  # whitespace only
                    {"author_name": "D", "rating": 3, "text": "Also has text"},
                ]
            }
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
            result = _fetch_place_details("PID", "fake-key")
        assert len(result["reviews"]) == 2
        assert {r["author"] for r in result["reviews"]} == {"A", "D"}

    def test_review_field_normalization(self):
        payload = {
            "result": {
                "reviews": [
                    {"author_name": "Jane", "rating": 5, "text": "X", "relative_time_description": "1 week ago"},
                ]
            }
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
            result = _fetch_place_details("PID", "fake-key")
        r = result["reviews"][0]
        # Verify our normalized keys (not Google's raw keys)
        assert set(r.keys()) == {"author", "rating", "text", "time_ago"}
        assert r["author"] == "Jane"
        assert r["time_ago"] == "1 week ago"

    def test_handles_missing_optional_fields_gracefully(self):
        payload = {"result": {}}  # no rating, no reviews, no address
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
            result = _fetch_place_details("PID", "fake-key")
        assert result["rating"] == 0
        assert result["total_ratings"] == 0
        assert result["reviews"] == []
        assert result["address"] == ""

    def test_opening_hours_joined_into_hours_string(self):
        payload = {
            "result": {
                "opening_hours": {
                    "weekday_text": [
                        "Monday: 9:00 AM – 9:00 PM",
                        "Tuesday: 9:00 AM – 9:00 PM",
                    ]
                }
            }
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
            result = _fetch_place_details("PID", "fake-key")
        assert "Monday" in result["hours"]
        assert "Tuesday" in result["hours"]

    def test_network_error_returns_empty_dict(self):
        with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
            result = _fetch_place_details("PID", "fake-key")
        assert result == {}
