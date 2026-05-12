"""
Tests for intel.py — extract_images, fetch_site_content, grade_site location handling.

Run:
    pytest test_intel.py -v
"""
import pytest
from unittest.mock import patch, MagicMock

from intel import extract_images, fetch_site_content, grade_site, fetch_with_firecrawl


# ─── extract_images ───────────────────────────────────────────────────────────

class TestExtractImages:

    def test_og_image_property_before_content(self):
        html = '<meta property="og:image" content="https://example.com/hero.jpg">'
        assert extract_images(html, "https://example.com") == ["https://example.com/hero.jpg"]

    def test_og_image_content_before_property(self):
        html = '<meta content="https://example.com/hero.jpg" property="og:image">'
        assert extract_images(html, "https://example.com") == ["https://example.com/hero.jpg"]

    def test_twitter_image(self):
        html = '<meta name="twitter:image" content="https://example.com/tw.jpg">'
        assert extract_images(html, "https://example.com") == ["https://example.com/tw.jpg"]

    def test_twitter_image_content_before_name(self):
        html = '<meta content="https://example.com/tw.jpg" name="twitter:image">'
        assert extract_images(html, "https://example.com") == ["https://example.com/tw.jpg"]

    def test_og_image_takes_priority_over_twitter(self):
        html = (
            '<meta property="og:image" content="https://example.com/og.jpg">'
            '<meta name="twitter:image" content="https://example.com/tw.jpg">'
        )
        result = extract_images(html, "https://example.com")
        assert result[0] == "https://example.com/og.jpg"
        assert "https://example.com/tw.jpg" in result

    def test_img_tag_fallback(self):
        html = '<img src="/photos/interior.jpg" alt="Interior">'
        assert extract_images(html, "https://myrestaurant.com") == [
            "https://myrestaurant.com/photos/interior.jpg"
        ]

    def test_relative_url_made_absolute(self):
        html = '<meta property="og:image" content="/images/hero.png">'
        assert extract_images(html, "https://example.com") == ["https://example.com/images/hero.png"]

    def test_svg_skipped(self):
        html = '<img src="/logo.svg"> <img src="/photo.jpg">'
        result = extract_images(html, "https://example.com")
        assert "https://example.com/logo.svg" not in result
        assert "https://example.com/photo.jpg" in result

    def test_favicon_ico_skipped(self):
        html = '<img src="/favicon.ico"> <img src="/hero.jpg">'
        result = extract_images(html, "https://example.com")
        assert not any("favicon" in u for u in result)
        assert "https://example.com/hero.jpg" in result

    def test_tracking_pixel_skipped(self):
        html = '<img src="/pixel?id=123"> <img src="/hero.jpg">'
        result = extract_images(html, "https://example.com")
        assert not any("pixel" in u for u in result)
        assert "https://example.com/hero.jpg" in result

    def test_1x1_beacon_skipped(self):
        html = '<img src="/beacon/1x1.png"> <img src="/hero.jpg">'
        result = extract_images(html, "https://example.com")
        assert not any("beacon" in u for u in result)

    def test_data_uri_skipped(self):
        html = '<img src="data:image/png;base64,abc123"> <img src="/hero.jpg">'
        result = extract_images(html, "https://example.com")
        assert not any(u.startswith("data:") for u in result)
        assert "https://example.com/hero.jpg" in result

    def test_deduplication(self):
        html = (
            '<meta property="og:image" content="https://example.com/hero.jpg">'
            '<img src="/hero.jpg">'
        )
        result = extract_images(html, "https://example.com")
        assert result.count("https://example.com/hero.jpg") == 1

    def test_max_five_results(self):
        imgs = "".join(f'<img src="/photo{i}.jpg">' for i in range(10))
        result = extract_images(imgs, "https://example.com")
        assert len(result) <= 5

    def test_empty_html_returns_empty_list(self):
        assert extract_images("", "https://example.com") == []

    def test_no_images_in_html_returns_empty_list(self):
        html = "<html><body><p>No images here at all</p></body></html>"
        assert extract_images(html, "https://example.com") == []

    def test_non_http_scheme_excluded(self):
        html = '<img src="ftp://example.com/img.jpg"> <img src="/valid.jpg">'
        result = extract_images(html, "https://example.com")
        assert all(u.startswith(("http://", "https://")) for u in result)

    def test_all_results_are_absolute_urls(self):
        html = (
            '<meta property="og:image" content="/og.jpg">'
            '<img src="/photo1.jpg">'
            '<img src="/photo2.jpg">'
        )
        result = extract_images(html, "https://example.com")
        assert all(u.startswith("https://example.com") for u in result)

    def test_full_restaurant_page(self):
        html = (
            '<html><head>'
            '<meta property="og:image" content="https://thelittledoor.com/images/hero.jpg">'
            '<meta name="twitter:image" content="https://thelittledoor.com/images/twitter.jpg">'
            '</head><body>'
            '<img src="/images/interior.jpg" alt="Interior">'
            '<img src="/logo.svg">'
            '</body></html>'
        )
        result = extract_images(html, "https://thelittledoor.com")
        assert result[0] == "https://thelittledoor.com/images/hero.jpg"
        assert len(result) == 3
        assert not any(".svg" in u for u in result)


# ─── Location fallback ────────────────────────────────────────────────────────

def _base_intel(**overrides):
    base = {
        "business_name": "Test Biz",
        "tagline": "Great stuff",
        "description": "A business",
        "services": [],
        "location": "Austin, TX",
        "phone": "512-555-0000",
        "email": "hi@test.com",
        "hours": "9am-5pm",
        "social_proof": "100 happy customers",
        "key_cta": "Book now",
        "missing": "no chat",
    }
    base.update(overrides)
    return base


class TestLocationFallback:

    def test_scrape_site_location_default_is_empty_string(self):
        """When Claude returns no location, intel.location must be '' not 'San Diego, CA'."""
        with patch("intel.fetch_site_content", return_value=("some text", [])), \
             patch("intel.extract_intel_with_claude", return_value={}), \
             patch("os.makedirs"), \
             patch("builtins.open", MagicMock()):
            from intel import scrape_site
            intel = scrape_site("example.com")
        assert intel["location"] == ""
        assert intel["location"] != "San Diego, CA"

    def test_scrape_site_preserves_real_location(self):
        """When Claude extracts a real location, it must be kept exactly."""
        with patch("intel.fetch_site_content", return_value=("some text", [])), \
             patch("intel.extract_intel_with_claude", return_value={"location": "Austin, TX"}), \
             patch("os.makedirs"), \
             patch("builtins.open", MagicMock()):
            from intel import scrape_site
            intel = scrape_site("example.com")
        assert intel["location"] == "Austin, TX"

    def test_grade_site_gives_contact_points_for_any_real_location(self):
        """Any non-empty location scores, not just non-SD locations."""
        intel = _base_intel(location="Portland, OR")
        grade = grade_site(intel)
        # phone(4) + email(3) + location(3) = 10
        assert grade["scores"]["contact"] == 10

    def test_grade_site_no_contact_points_for_empty_location(self):
        """Empty location (scrape failed) must not score contact points for location."""
        intel = _base_intel(location="", email="")
        grade = grade_site(intel)
        # phone(4) only
        assert grade["scores"]["contact"] == 4

    def test_grade_site_no_longer_penalises_san_diego_location(self):
        """San Diego businesses are real prospects — their location should score."""
        intel = _base_intel(location="San Diego, CA")
        grade = grade_site(intel)
        assert grade["scores"]["contact"] == 10

    def test_grade_site_zero_contact_when_all_missing(self):
        intel = _base_intel(location="", phone="", email="")
        grade = grade_site(intel)
        assert grade["scores"]["contact"] == 0

# ─── fetch_site_content ───────────────────────────────────────────────────────

class TestFetchSiteContent:

    def _mock_get(self, html: str):
        resp = MagicMock()
        resp.text = html
        return resp

    def test_returns_two_element_tuple(self):
        with patch("requests.get", return_value=self._mock_get("<p>Hello</p>")):
            result = fetch_site_content("example.com")
        assert isinstance(result, tuple) and len(result) == 2

    def test_strips_html_tags_from_text(self):
        with patch("requests.get", return_value=self._mock_get("<h1>Welcome</h1><p>Great food</p>")):
            text, _ = fetch_site_content("example.com")
        assert "<h1>" not in text
        assert "Welcome" in text
        assert "Great food" in text

    def test_strips_script_blocks(self):
        with patch("requests.get", return_value=self._mock_get("<script>var x=1;</script><p>Content</p>")):
            text, _ = fetch_site_content("example.com")
        assert "var x" not in text
        assert "Content" in text

    def test_strips_style_blocks(self):
        with patch("requests.get", return_value=self._mock_get("<style>.foo{color:red}</style><p>ok</p>")):
            text, _ = fetch_site_content("example.com")
        assert ".foo" not in text

    def test_extracts_og_image_into_images_list(self):
        html = (
            '<html><head>'
            '<meta property="og:image" content="https://example.com/hero.jpg">'
            '</head><body><p>Content</p></body></html>'
        )
        with patch("requests.get", return_value=self._mock_get(html)):
            _, images = fetch_site_content("example.com")
        assert "https://example.com/hero.jpg" in images

    def test_text_truncated_to_4000_chars(self):
        with patch("requests.get", return_value=self._mock_get("<p>" + "x" * 10000 + "</p>")):
            text, _ = fetch_site_content("example.com")
        assert len(text) <= 4000

    def test_network_error_returns_empty_string_and_list(self):
        with patch("requests.get", side_effect=Exception("Connection refused")):
            text, images = fetch_site_content("unreachable.com")
        assert text == ""
        assert images == []

    def test_prepends_https_to_bare_domain(self):
        with patch("requests.get", return_value=self._mock_get("<p>ok</p>")) as mock_get:
            fetch_site_content("example.com")
        assert mock_get.call_args[0][0] == "https://example.com"

    def test_does_not_double_prepend_https(self):
        with patch("requests.get", return_value=self._mock_get("<p>ok</p>")) as mock_get:
            fetch_site_content("https://example.com")
        assert mock_get.call_args[0][0] == "https://example.com"

    def test_images_empty_when_no_image_tags(self):
        with patch("requests.get", return_value=self._mock_get("<html><body><p>text only</p></body></html>")):
            _, images = fetch_site_content("example.com")
        assert images == []


# ─── fetch_with_firecrawl ─────────────────────────────────────────────────────

class TestFetchWithFirecrawl:

    def _mock_urlopen(self, markdown="", html="", og_image="", success=True):
        resp_data = {
            "success": success,
            "data": {
                "markdown": markdown,
                "html": html,
                "metadata": {"ogImage": og_image},
            },
        }
        import json as _json
        mock_resp = MagicMock()
        mock_resp.read.return_value = _json.dumps(resp_data).encode()
        mock_resp.__enter__ = lambda s: mock_resp
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    def test_returns_empty_when_no_api_key(self):
        with patch.dict("os.environ", {"FIRECRAWL_API_KEY": ""}):
            text, images = fetch_with_firecrawl("example.com")
        assert text == ""
        assert images == []

    def test_returns_markdown_text_on_success(self):
        with patch.dict("os.environ", {"FIRECRAWL_API_KEY": "fc-test"}), \
             patch("urllib.request.urlopen", return_value=self._mock_urlopen(markdown="# Hello\nGreat restaurant")):
            text, _ = fetch_with_firecrawl("example.com")
        assert "Great restaurant" in text

    def test_returns_og_image_from_metadata(self):
        with patch.dict("os.environ", {"FIRECRAWL_API_KEY": "fc-test"}), \
             patch("urllib.request.urlopen", return_value=self._mock_urlopen(og_image="https://example.com/hero.jpg")):
            _, images = fetch_with_firecrawl("example.com")
        assert "https://example.com/hero.jpg" in images

    def test_og_image_is_first_in_list(self):
        html = '<img src="/photo.jpg">'
        with patch.dict("os.environ", {"FIRECRAWL_API_KEY": "fc-test"}), \
             patch("urllib.request.urlopen", return_value=self._mock_urlopen(og_image="https://example.com/og.jpg", html=html)):
            _, images = fetch_with_firecrawl("example.com")
        assert images[0] == "https://example.com/og.jpg"

    def test_returns_empty_on_success_false(self):
        with patch.dict("os.environ", {"FIRECRAWL_API_KEY": "fc-test"}), \
             patch("urllib.request.urlopen", return_value=self._mock_urlopen(success=False)):
            text, images = fetch_with_firecrawl("example.com")
        assert text == ""
        assert images == []

    def test_returns_empty_on_network_error(self):
        with patch.dict("os.environ", {"FIRECRAWL_API_KEY": "fc-test"}), \
             patch("urllib.request.urlopen", side_effect=Exception("timeout")):
            text, images = fetch_with_firecrawl("example.com")
        assert text == ""
        assert images == []

    def test_text_truncated_to_4000_chars(self):
        long_md = "x" * 10000
        with patch.dict("os.environ", {"FIRECRAWL_API_KEY": "fc-test"}), \
             patch("urllib.request.urlopen", return_value=self._mock_urlopen(markdown=long_md)):
            text, _ = fetch_with_firecrawl("example.com")
        assert len(text) <= 4000

    def test_max_five_images_returned(self):
        html = "".join(f'<img src="/photo{i}.jpg">' for i in range(10))
        with patch.dict("os.environ", {"FIRECRAWL_API_KEY": "fc-test"}), \
             patch("urllib.request.urlopen", return_value=self._mock_urlopen(html=html)):
            _, images = fetch_with_firecrawl("example.com")
        assert len(images) <= 5


# ─── scrape_site Firecrawl fallback ──────────────────────────────────────────

class TestScrapeSiteFirecrawlFallback:

    def test_uses_firecrawl_when_key_set(self):
        with patch("intel.fetch_with_firecrawl", return_value=("firecrawl content", [])) as mock_fc, \
             patch("intel.extract_intel_with_claude", return_value={}), \
             patch("os.makedirs"), patch("builtins.open", MagicMock()):
            from intel import scrape_site
            scrape_site("example.com")
        mock_fc.assert_called_once()

    def test_falls_back_to_requests_when_firecrawl_empty(self):
        with patch("intel.fetch_with_firecrawl", return_value=("", [])), \
             patch("intel.fetch_site_content", return_value=("requests content", [])) as mock_req, \
             patch("intel.extract_intel_with_claude", return_value={}), \
             patch("os.makedirs"), patch("builtins.open", MagicMock()):
            from intel import scrape_site
            scrape_site("example.com")
        mock_req.assert_called_once()

    def test_does_not_call_requests_when_firecrawl_succeeds(self):
        with patch("intel.fetch_with_firecrawl", return_value=("good content", [])), \
             patch("intel.fetch_site_content") as mock_req, \
             patch("intel.extract_intel_with_claude", return_value={}), \
             patch("os.makedirs"), patch("builtins.open", MagicMock()):
            from intel import scrape_site
            scrape_site("example.com")
        mock_req.assert_not_called()


# ─── scrape_site intel dict fields ───────────────────────────────────────────

class TestScrapeSiteIntelFields:

    def test_content_notes_present_in_intel_dict(self):
        """scrape_site must return content_notes key."""
        with patch("intel.fetch_with_firecrawl", return_value=("", [])), \
             patch("intel.fetch_site_content", return_value=("some text", [])), \
             patch("intel.extract_intel_with_claude", return_value={}), \
             patch("os.makedirs"), patch("builtins.open", MagicMock()):
            from intel import scrape_site
            intel = scrape_site("example.com")
        assert "content_notes" in intel

    def test_content_notes_from_extracted(self):
        """content_notes must propagate from Claude extraction."""
        expected = "Tacos: $4 each, Burritos: $8, Nachos: $10"
        with patch("intel.fetch_with_firecrawl", return_value=("", [])), \
             patch("intel.fetch_site_content", return_value=("some text", [])), \
             patch("intel.extract_intel_with_claude", return_value={"content_notes": expected}), \
             patch("os.makedirs"), patch("builtins.open", MagicMock()):
            from intel import scrape_site
            intel = scrape_site("example.com")
        assert intel["content_notes"] == expected

    def test_content_notes_defaults_to_empty_string(self):
        """content_notes defaults to '' when Claude returns nothing."""
        with patch("intel.fetch_with_firecrawl", return_value=("", [])), \
             patch("intel.fetch_site_content", return_value=("some text", [])), \
             patch("intel.extract_intel_with_claude", return_value={}), \
             patch("os.makedirs"), patch("builtins.open", MagicMock()):
            from intel import scrape_site
            intel = scrape_site("example.com")
        assert intel["content_notes"] == ""

    def test_raw_text_stored_at_4000_chars(self):
        """raw_text in intel dict must be full 4000 chars, not 1000."""
        long_text = "A" * 5000
        with patch("intel.fetch_with_firecrawl", return_value=("", [])), \
             patch("intel.fetch_site_content", return_value=(long_text[:4000], [])), \
             patch("intel.extract_intel_with_claude", return_value={}), \
             patch("os.makedirs"), patch("builtins.open", MagicMock()):
            from intel import scrape_site
            intel = scrape_site("example.com")
        assert len(intel["raw_text"]) == 4000

    def test_raw_text_not_truncated_at_1000(self):
        """raw_text must NOT be silently cut to 1000 chars."""
        long_text = "B" * 4000
        with patch("intel.fetch_with_firecrawl", return_value=("", [])), \
             patch("intel.fetch_site_content", return_value=(long_text, [])), \
             patch("intel.extract_intel_with_claude", return_value={}), \
             patch("os.makedirs"), patch("builtins.open", MagicMock()):
            from intel import scrape_site
            intel = scrape_site("example.com")
        assert len(intel["raw_text"]) > 1000


# ─── extract_intel_with_claude prompt coverage ───────────────────────────────

class TestExtractIntelPrompt:

    def _call_and_capture_prompt(self, mock_response: dict) -> str:
        """Run extract_intel_with_claude and return the prompt sent to Claude."""
        import json as _json
        captured = {}
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=_json.dumps(mock_response))]
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_msg

        with patch("intel._get_client", return_value=mock_client):
            from intel import extract_intel_with_claude
            extract_intel_with_claude("example.com", "some site content")

        call_kwargs = mock_client.messages.create.call_args
        captured["prompt"] = call_kwargs[1]["messages"][0]["content"]
        captured["max_tokens"] = call_kwargs[1]["max_tokens"]
        return captured

    def test_prompt_includes_content_notes_field(self):
        result = self._call_and_capture_prompt({})
        assert "content_notes" in result["prompt"]

    def test_prompt_asks_for_menu_items_and_pricing(self):
        result = self._call_and_capture_prompt({})
        prompt = result["prompt"]
        assert "menu" in prompt.lower() or "price" in prompt.lower() or "pricing" in prompt.lower()

    def test_max_tokens_increased_to_2000(self):
        result = self._call_and_capture_prompt({})
        assert result["max_tokens"] == 2000


# ─── scrape_site Google Places enrichment ────────────────────────────────────

class TestScrapeSitePlacesEnrichment:

    def test_intel_includes_reviews_from_places(self):
        sample_reviews = [
            {"author": "Jane", "rating": 5, "text": "Best ever!", "time_ago": "2 weeks ago"}
        ]
        with patch("intel.fetch_with_firecrawl", return_value=("text", [])), \
             patch("intel.extract_intel_with_claude", return_value={"business_name": "Test"}), \
             patch("intel.fetch_place_data", return_value={
                 "place_id": "PID",
                 "rating": 4.7,
                 "total_ratings": 200,
                 "reviews": sample_reviews,
                 "address": "",
                 "phone": "",
                 "hours": "",
             }), \
             patch("os.makedirs"), patch("builtins.open", MagicMock()):
            from intel import scrape_site
            intel = scrape_site("example.com")
        assert intel["reviews"] == sample_reviews
        assert intel["google_rating"] == 4.7
        assert intel["google_total_ratings"] == 200
        assert intel["google_place_id"] == "PID"

    def test_reviews_default_to_empty_list_when_places_unavailable(self):
        with patch("intel.fetch_with_firecrawl", return_value=("text", [])), \
             patch("intel.extract_intel_with_claude", return_value={}), \
             patch("intel.fetch_place_data", return_value={}), \
             patch("os.makedirs"), patch("builtins.open", MagicMock()):
            from intel import scrape_site
            intel = scrape_site("example.com")
        assert intel["reviews"] == []
        assert intel["google_rating"] == 0
        assert intel["google_total_ratings"] == 0

    def test_places_phone_used_when_scraped_phone_missing(self):
        with patch("intel.fetch_with_firecrawl", return_value=("text", [])), \
             patch("intel.extract_intel_with_claude", return_value={"phone": ""}), \
             patch("intel.fetch_place_data", return_value={"phone": "(619) 555-1234"}), \
             patch("os.makedirs"), patch("builtins.open", MagicMock()):
            from intel import scrape_site
            intel = scrape_site("example.com")
        assert intel["phone"] == "(619) 555-1234"

    def test_scraped_phone_preferred_over_places_phone(self):
        with patch("intel.fetch_with_firecrawl", return_value=("text", [])), \
             patch("intel.extract_intel_with_claude", return_value={"phone": "555-0000"}), \
             patch("intel.fetch_place_data", return_value={"phone": "(619) 555-1234"}), \
             patch("os.makedirs"), patch("builtins.open", MagicMock()):
            from intel import scrape_site
            intel = scrape_site("example.com")
        # Site-scraped phone wins (it's the number the business actually publishes)
        assert intel["phone"] == "555-0000"

    def test_places_hours_used_when_scraped_hours_missing(self):
        with patch("intel.fetch_with_firecrawl", return_value=("text", [])), \
             patch("intel.extract_intel_with_claude", return_value={"hours": ""}), \
             patch("intel.fetch_place_data", return_value={"hours": "Mon: 9-5"}), \
             patch("os.makedirs"), patch("builtins.open", MagicMock()):
            from intel import scrape_site
            intel = scrape_site("example.com")
        assert intel["hours"] == "Mon: 9-5"

    def test_places_address_used_when_scraped_location_missing(self):
        with patch("intel.fetch_with_firecrawl", return_value=("text", [])), \
             patch("intel.extract_intel_with_claude", return_value={"location": ""}), \
             patch("intel.fetch_place_data", return_value={"address": "123 Main St"}), \
             patch("os.makedirs"), patch("builtins.open", MagicMock()):
            from intel import scrape_site
            intel = scrape_site("example.com")
        assert intel["location"] == "123 Main St"

    def test_places_called_with_business_name_and_location(self):
        with patch("intel.fetch_with_firecrawl", return_value=("text", [])), \
             patch("intel.extract_intel_with_claude", return_value={"business_name": "Test Bistro", "location": "San Diego"}), \
             patch("intel.fetch_place_data", return_value={}) as mock_places, \
             patch("os.makedirs"), patch("builtins.open", MagicMock()):
            from intel import scrape_site
            scrape_site("example.com")
        mock_places.assert_called_once_with("Test Bistro", "San Diego")
