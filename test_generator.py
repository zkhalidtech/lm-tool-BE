"""
Tests for generator.py — 2-pass site generation.

Tests are split into:
  - TestPart1Prompt     : _build_part1_prompt content (no Claude call needed)
  - TestPart2Prompt     : _build_part2_prompt content (no Claude call needed)
  - TestStripFences     : _strip_fences helper
  - TestTwoCallBehavior : generate_site makes 2 calls, prefills correctly, stitches output
  - TestHeroImage       : image/gradient rules flow through to the part1 prompt
  - TestRegression      : key sections still present, nothing broken

Run:
    pytest test_generator.py -v
"""
import pytest
from unittest.mock import patch, MagicMock, mock_open, call

from generator import _build_part1_prompt, _build_part2_prompt, _build_footer, _clean_html, _close_unclosed_elements, _is_light_color, _strip_fences, _is_image_reachable
from config import BOOKING_URL


# ─── Shared fixtures ──────────────────────────────────────────────────────────

INTEL = {
    "domain": "testbistro.com",
    "business_name": "Test Bistro",
    "description": "A great restaurant in San Diego",
    "services": ["Dining", "Catering", "Private Events"],
    "location": "Gaslamp, San Diego, CA",
    "phone": "619-555-0000",
    "hours": "Mon-Sun 11am-10pm",
    "social_proof": "Best of SD 2023, 500+ reviews",
    "brand_vibe": "warm and modern",
    "primary_color": "#1a1a2e",
    "secondary_color": "#c9a961",
    "business_type": "restaurant",
    "missing": "no chat widget, no online booking",
    "key_cta": "Reserve a Table",
    "tagline": "Fresh food, great vibes",
    "pain_point": "No online booking",
    "chat_persona": "Friendly host",
    "cta_angle": "Reserve a Table",
    "email": "hi@testbistro.com",
    "owner_name": "",
    "neighborhood": "Gaslamp",
    "content_notes": "Signature Tacos al Pastor: $4, Carne Asada Burrito: $12, Daily Happy Hour 4-7pm",
    "images": [],
    "raw_text": "Great restaurant in the Gaslamp Quarter.",
}

PART1_HTML = (
    "<!DOCTYPE html><html><head><style>*{margin:0}</style></head>"
    "<body>\n<div>CLAIM BAR</div><nav>NAV</nav>"
    "<section>HERO</section><section>SOCIAL PROOF</section>"
    "<section>SERVICES</section>\n<!-- CONTINUE -->"
)
PART2_HTML = (
    "<section>TESTIMONIALS</section>"
    "<section>CTA BANNER</section>"
    "<footer>FOOTER</footer>\n</body></html>"
)


def _run(intel_override=None, part1=PART1_HTML, part2=PART2_HTML, image_reachable=True):
    """
    Run generate_site with mocked Claude + filesystem.
    Returns (create_calls, written_html).
    image_reachable: bool or callable(url) -> bool passed to _is_image_reachable mock.
    """
    intel = {**INTEL, **(intel_override or {})}

    msg1 = MagicMock()
    msg1.content = [MagicMock(text=part1)]
    msg2 = MagicMock()
    msg2.content = [MagicMock(text=part2)]

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [msg1, msg2]

    written = []
    mo = mock_open()
    mo.return_value.__enter__.return_value.write.side_effect = lambda x: written.append(x)

    reach_kwargs = (
        {"side_effect": image_reachable}
        if callable(image_reachable)
        else {"return_value": image_reachable}
    )

    with patch("anthropic.Anthropic", return_value=mock_client), \
         patch("generator._is_image_reachable", **reach_kwargs), \
         patch("os.makedirs"), \
         patch("builtins.open", mo):
        from generator import generate_site
        generate_site(intel, "test-bistro")

    return mock_client.messages.create.call_args_list, "".join(written)


# ─── _strip_fences ────────────────────────────────────────────────────────────

class TestStripFences:

    def test_plain_html_unchanged(self):
        html = "<!DOCTYPE html><html></html>"
        assert _strip_fences(html) == html

    def test_strips_generic_fence(self):
        raw = "```\n<!DOCTYPE html>\n```"
        assert _strip_fences(raw) == "<!DOCTYPE html>"

    def test_strips_html_fence(self):
        raw = "```html\n<!DOCTYPE html>\n```"
        assert _strip_fences(raw) == "<!DOCTYPE html>"

    def test_strips_leading_trailing_whitespace(self):
        assert _strip_fences("  hello  ") == "hello"


# ─── _build_part1_prompt ──────────────────────────────────────────────────────

class TestPart1Prompt:

    def _prompt(self, image_rule="- no images", hero_bg="gradient"):
        return _build_part1_prompt(INTEL, "", hero_bg, image_rule)

    def test_contains_claim_bar(self):
        assert "CLAIM BAR" in self._prompt()

    def test_contains_nav(self):
        assert "NAV" in self._prompt()

    def test_contains_hero(self):
        assert "HERO" in self._prompt()

    def test_contains_social_proof(self):
        assert "SOCIAL PROOF" in self._prompt()

    def test_contains_services(self):
        assert "SERVICES" in self._prompt()

    def test_does_not_contain_testimonials(self):
        assert "TESTIMONIALS" not in self._prompt()

    def test_does_not_contain_cta_banner(self):
        assert "CTA BANNER" not in self._prompt()

    def test_does_not_contain_footer_section(self):
        assert "FOOTER" not in self._prompt()

    def test_instructs_to_end_with_continue_marker(self):
        assert "<!-- CONTINUE -->" in self._prompt()

    def test_instructs_not_to_close_body(self):
        prompt = self._prompt()
        assert "Do NOT write </body>" in prompt

    def test_booking_url_present(self):
        assert BOOKING_URL in self._prompt()

    def test_business_name_present(self):
        assert "Test Bistro" in self._prompt()

    def test_primary_color_present(self):
        assert "#1a1a2e" in self._prompt()

    def test_secondary_color_present(self):
        assert "#c9a961" in self._prompt()

    def test_services_listed(self):
        prompt = self._prompt()
        assert "Dining" in prompt

    def test_image_rule_injected(self):
        prompt = _build_part1_prompt(INTEL, "", "gradient", "- NO external image URLs")
        assert "NO external image URLs" in prompt

    def test_hero_bg_instruction_injected(self):
        prompt = _build_part1_prompt(INTEL, "", "background: linear-gradient(red,blue)", "- no images")
        assert "linear-gradient(red,blue)" in prompt

    def test_notes_block_injected_when_present(self):
        prompt = _build_part1_prompt(INTEL, "\n\nSPECIAL: Use blue everywhere\n", "g", "r")
        assert "SPECIAL: Use blue everywhere" in prompt

    def test_no_notes_block_when_empty(self):
        prompt = self._prompt()
        assert "SPECIAL INSTRUCTIONS" not in prompt


# ─── _build_part2_prompt ──────────────────────────────────────────────────────

class TestPart2Prompt:

    def _prompt(self):
        return _build_part2_prompt(INTEL)

    def test_contains_testimonials(self):
        assert "TESTIMONIALS" in self._prompt()

    def test_contains_cta_banner(self):
        assert "CTA BANNER" in self._prompt()

    def test_does_not_contain_footer_section(self):
        assert "8. FOOTER" not in self._prompt()

    def test_does_not_contain_claim_bar(self):
        assert "CLAIM BAR" not in self._prompt()

    def test_does_not_contain_hero(self):
        assert "HERO" not in self._prompt()

    def test_does_not_instruct_to_close_body_html(self):
        assert "Do NOT write </body>" in self._prompt()

    def test_social_proof_referenced(self):
        assert "Best of SD 2023" in self._prompt()

    def test_cta_angle_referenced(self):
        assert "Reserve a Table" in self._prompt()

    def test_city_extracted_from_location(self):
        assert "Gaslamp" in self._prompt()

    def test_pain_point_referenced(self):
        assert "No online booking" in self._prompt()

    def test_phone_not_in_part2_prompt(self):
        # Phone is in Python-generated footer, not Part 2 prompt
        assert "619-555-0000" not in self._prompt()

    # B21 — brand variables re-injected
    def test_primary_color_in_part2_prompt(self):
        assert "#1a1a2e" in self._prompt()

    def test_secondary_color_in_part2_prompt(self):
        assert "#c9a961" in self._prompt()

    def test_brand_vibe_in_part2_prompt(self):
        assert "warm and modern" in self._prompt()

    def test_no_new_colors_instruction_in_part2_prompt(self):
        assert "never introduce new colors" in self._prompt()

    def test_same_google_font_instruction_in_part2_prompt(self):
        assert "same Google Font" in self._prompt()

    def test_different_primary_color_reflected_in_part2_prompt(self):
        intel = {**INTEL, "primary_color": "#ff0000"}
        prompt = _build_part2_prompt(intel)
        assert "#ff0000" in prompt

    def test_different_secondary_color_reflected_in_part2_prompt(self):
        intel = {**INTEL, "secondary_color": "#00ff00"}
        prompt = _build_part2_prompt(intel)
        assert "#00ff00" in prompt


# ─── Two-call behavior ────────────────────────────────────────────────────────

class TestTwoCallBehavior:

    def test_exactly_two_claude_calls(self):
        calls, _ = _run()
        assert len(calls) == 2

    def test_call1_max_tokens_is_6000(self):
        calls, _ = _run()
        assert calls[0][1]["max_tokens"] == 6000

    def test_call2_max_tokens_is_4000(self):
        # Pass 2 only renders the CTA banner now (testimonials + footer are Python-built).
        # 4000 tokens gives Claude plenty of room to finish the section + button cleanly.
        calls, _ = _run()
        assert calls[1][1]["max_tokens"] == 4000

    def test_call2_is_user_only_no_assistant_prefill(self):
        # claude-opus-4-7 doesn't support assistant message prefill. Pass 2 now
        # passes Part 1 as context inside a single user message.
        calls, _ = _run()
        msgs = calls[1][1]["messages"]
        roles = [m["role"] for m in msgs]
        assert "assistant" not in roles
        assert roles == ["user"]

    def test_call2_user_message_contains_part1_context(self):
        # The Part 1 HTML is embedded inside Pass 2's user message so Claude
        # can match the existing design system. CLAIM BAR is a Part 1 section.
        calls, _ = _run()
        msgs = calls[1][1]["messages"]
        user_content = msgs[0]["content"]
        assert "CLAIM BAR" in user_content

    def test_continue_marker_stripped_from_pass2_context(self):
        calls, _ = _run()
        msgs = calls[1][1]["messages"]
        user_content = msgs[0]["content"]
        assert "<!-- CONTINUE -->" not in user_content

    def test_continue_marker_not_in_final_html(self):
        _, html = _run()
        assert "<!-- CONTINUE -->" not in html

    def test_both_parts_stitched_in_output(self):
        _, html = _run()
        assert "SERVICES" in html       # from part1
        assert "TESTIMONIALS" in html   # from part2

    def test_final_html_closes_with_html_tag(self):
        _, html = _run()
        assert "</html>" in html

    def test_chat_widget_injected_before_body_close(self):
        _, html = _run()
        assert "lvrg-chat-host" in html
        assert html.index("lvrg-chat-host") < html.index("</body>")

    def test_part1_without_continue_marker_still_works(self):
        part1_no_marker = PART1_HTML.replace("<!-- CONTINUE -->", "")
        calls, html = _run(part1=part1_no_marker)
        assert len(calls) == 2
        assert "TESTIMONIALS" in html

    def test_truncated_part2_still_gets_footer_and_closed(self):
        truncated_part2 = "<section>TESTIMONIALS</section>"  # no </body></html>
        _, html = _run(part2=truncated_part2)
        assert "</html>" in html
        assert "lvrg-footer" in html or "<footer" in html


# ─── Hero image — flows through to Call 1 prompt ─────────────────────────────

class TestHeroImage:

    def test_real_image_url_in_call1_prompt(self):
        calls, _ = _run({"images": ["https://testbistro.com/hero.jpg"]})
        prompt = calls[0][1]["messages"][0]["content"]
        assert "https://testbistro.com/hero.jpg" in prompt

    def test_dark_overlay_in_call1_prompt_when_image_present(self):
        calls, _ = _run({"images": ["https://testbistro.com/hero.jpg"]})
        prompt = calls[0][1]["messages"][0]["content"]
        assert "rgba(0,0,0" in prompt

    def test_gradient_fallback_present_even_with_image(self):
        calls, _ = _run({"images": ["https://testbistro.com/hero.jpg"]})
        prompt = calls[0][1]["messages"][0]["content"]
        assert "linear-gradient" in prompt

    def test_no_external_url_in_prompt_when_no_images(self):
        calls, _ = _run({"images": []})
        prompt = calls[0][1]["messages"][0]["content"]
        assert "url('http" not in prompt

    def test_restriction_message_when_no_images(self):
        calls, _ = _run({"images": []})
        prompt = calls[0][1]["messages"][0]["content"]
        assert "NO external image URLs" in prompt

    def test_no_restriction_message_when_image_present(self):
        calls, _ = _run({"images": ["https://testbistro.com/hero.jpg"]})
        prompt = calls[0][1]["messages"][0]["content"]
        assert "NO external image URLs" not in prompt

    def test_uses_first_image_only(self):
        calls, _ = _run({"images": [
            "https://testbistro.com/hero1.jpg",
            "https://testbistro.com/hero2.jpg",
        ]})
        prompt = calls[0][1]["messages"][0]["content"]
        assert "https://testbistro.com/hero1.jpg" in prompt

    def test_missing_images_key_treated_as_no_images(self):
        intel = {k: v for k, v in INTEL.items() if k != "images"}
        calls, _ = _run(intel_override=intel)
        prompt = calls[0][1]["messages"][0]["content"]
        assert "NO external image URLs" in prompt


# ─── Regression — nothing broken ─────────────────────────────────────────────

class TestRegression:

    def test_booking_url_in_call1_prompt(self):
        calls, _ = _run()
        assert BOOKING_URL in calls[0][1]["messages"][0]["content"]

    def test_business_name_in_call1_prompt(self):
        calls, _ = _run()
        assert "Test Bistro" in calls[0][1]["messages"][0]["content"]

    def test_services_in_call1_prompt(self):
        calls, _ = _run()
        assert "Dining" in calls[0][1]["messages"][0]["content"]

    def test_social_proof_in_call2_prompt(self):
        calls, _ = _run()
        assert "Best of SD 2023" in calls[1][1]["messages"][0]["content"]

    def test_model_is_claude_opus(self):
        calls, _ = _run()
        assert calls[0][1]["model"] == "claude-opus-4-7"
        assert calls[1][1]["model"] == "claude-opus-4-7"


# ─── Location handling ────────────────────────────────────────────────────────

class TestLocationHandling:

    def test_real_location_passed_to_part1_prompt(self):
        prompt = _build_part1_prompt(INTEL, "", "gradient", "no images")
        assert "Gaslamp, San Diego, CA" in prompt

    def test_empty_location_shows_not_found_in_part1_prompt(self):
        intel = {**INTEL, "location": ""}
        prompt = _build_part1_prompt(intel, "", "gradient", "no images")
        assert "Not found" in prompt
        assert "do not invent" in prompt

    def test_empty_location_uses_their_city_fallback_in_part2_prompt(self):
        intel = {**INTEL, "location": ""}
        prompt = _build_part2_prompt(intel)
        assert "their city" in prompt

    def test_real_location_city_extracted_for_part2_copy_rules(self):
        prompt = _build_part2_prompt(INTEL)
        assert "Gaslamp" in prompt

    def test_empty_location_no_san_diego_invented_in_part1(self):
        intel = {**INTEL, "location": ""}
        prompt = _build_part1_prompt(intel, "", "gradient", "no images")
        assert "San Diego, CA" not in prompt

    def test_non_sd_location_preserved_in_part1_prompt(self):
        intel = {**INTEL, "location": "Austin, TX"}
        prompt = _build_part1_prompt(intel, "", "gradient", "no images")
        assert "Austin, TX" in prompt


# ─── Chat widget endpoint — CHAT_ENDPOINT env var ─────────────────────────────

class TestChatWidgetEndpoint:

    def test_default_endpoint_is_prod_railway_url(self):
        _, html = _run()
        assert "lvrg-engine-production.up.railway.app/chat" in html

    def test_chat_endpoint_env_var_overrides_default(self):
        with patch.dict("os.environ", {"CHAT_ENDPOINT": "http://localhost:8766/chat"}):
            _, html = _run()
        assert "http://localhost:8766/chat" in html

    def test_custom_endpoint_replaces_prod_url(self):
        with patch.dict("os.environ", {"CHAT_ENDPOINT": "http://localhost:8766/chat"}):
            _, html = _run()
        assert "lvrg-engine-production.up.railway.app" not in html

    def test_endpoint_is_in_widget_js(self):
        _, html = _run()
        # Shadow DOM widget stores endpoint in a local `endpoint` var; check the URL is in the HTML
        assert "/chat" in html
        assert "endpoint" in html

    def test_env_var_empty_string_falls_back_to_default(self):
        with patch.dict("os.environ", {"CHAT_ENDPOINT": ""}):
            _, html = _run()
        assert "lvrg-engine-production.up.railway.app/chat" in html


# ─── B15: Chat outside-click + Escape close ───────────────────────────────────

class TestChatOutsideClick:

    def test_document_click_listener_present(self):
        _, html = _run()
        assert "document.addEventListener('click'" in html

    def test_document_keydown_listener_present(self):
        _, html = _run()
        assert "document.addEventListener('keydown'" in html

    def test_escape_key_closes_panel(self):
        _, html = _run()
        assert "e.key==='Escape'" in html

    def test_outside_click_closes_panel(self):
        _, html = _run()
        # Outside click handler closes the panel (Shadow DOM stops propagation in panel/btn)
        assert "document.addEventListener('click',function(){close_();})" in html

    def test_close_function_defined(self):
        _, html = _run()
        assert "function close_()" in html

    def test_open_function_defined(self):
        _, html = _run()
        assert "function open_()" in html

    def test_chat_host_element_present(self):
        _, html = _run()
        assert 'id="lvrg-chat-host"' in html


# ─── CSS isolation: Shadow DOM ───────────────────────────────────────────────

class TestChatCSSIsolation:

    def test_uses_shadow_dom(self):
        _, html = _run()
        assert "attachShadow" in html

    def test_shadow_root_open_mode(self):
        _, html = _run()
        assert "{mode:'open'}" in html

    def test_shadow_styles_use_host_pseudo(self):
        _, html = _run()
        # `:host{all:initial...}` inside Shadow DOM (no global #lvrg-chat * selectors)
        assert ":host{all:initial" in html

    def test_box_sizing_reset_present(self):
        _, html = _run()
        assert "box-sizing:border-box" in html

    def test_system_font_stack_on_host(self):
        _, html = _run()
        assert "BlinkMacSystemFont" in html

    def test_widget_isolated_from_host_page_css(self):
        _, html = _run()
        # No global #lvrg-chat * selectors leaking to the host page
        assert "#lvrg-chat,#lvrg-chat *" not in html
        assert "#lvrg-chat *" not in html


# ─── B17: No duplicate chat widget — prompt instruction ───────────────────────

class TestNoDuplicateChatPrompt:

    def test_part1_prompt_forbids_chat_widget(self):
        prompt = _build_part1_prompt(INTEL, "", "gradient", "- no images")
        assert "LVRG injects its own" in prompt

    def test_part2_prompt_forbids_chat_widget(self):
        prompt = _build_part2_prompt(INTEL)
        assert "LVRG injects" in prompt

    def test_part1_prompt_forbids_floating_button(self):
        prompt = _build_part1_prompt(INTEL, "", "gradient", "- no images")
        assert "floating button" in prompt

    def test_part2_prompt_forbids_floating_button(self):
        prompt = _build_part2_prompt(INTEL)
        assert "floating button" in prompt


# ─── B18: UI quality — radius, hover, transitions in prompts ─────────────────

class TestUIQualityPrompt:

    def _p1(self):
        return _build_part1_prompt(INTEL, "", "gradient", "- no images")

    def _p2(self):
        return _build_part2_prompt(INTEL)

    # Part 1
    def test_p1_bans_form_elements(self):
        assert "Do NOT generate <form>" in self._p1() or "Do NOT generate" in self._p1()

    def test_p2_bans_form_elements(self):
        assert "NO form" in self._p2() or "Do NOT generate" in self._p2() or "No form" in self._p2()

    def test_p1_button_border_radius_instruction(self):
        assert "border-radius 8" in self._p1()

    def test_p1_card_border_radius_instruction(self):
        assert "border-radius 12" in self._p1()

    def test_p1_hover_onmouseover_instruction(self):
        assert "onmouseover" in self._p1()

    def test_p1_transition_instruction(self):
        assert "transition:all 0.2s ease" in self._p1()

    def test_p1_section_padding_instruction(self):
        assert "80px" in self._p1()

    def test_p1_card_box_shadow_instruction(self):
        assert "box-shadow" in self._p1()

    # Part 2 — same design rules repeated
    def test_p2_button_border_radius_instruction(self):
        assert "border-radius 8" in self._p2()

    def test_p2_card_border_radius_instruction(self):
        assert "border-radius 12" in self._p2()

    def test_p2_hover_onmouseover_instruction(self):
        assert "onmouseover" in self._p2()

    def test_p2_transition_instruction(self):
        assert "transition:all 0.2s ease" in self._p2()


# ─── B19: Image reachability — HEAD check + fallback ─────────────────────────

class TestIsImageReachable:

    def _mock_resp(self, status: int):
        resp = MagicMock()
        resp.status = status
        resp.__enter__ = lambda s: resp
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def test_returns_true_on_200(self):
        with patch("urllib.request.urlopen", return_value=self._mock_resp(200)):
            assert _is_image_reachable("https://example.com/hero.jpg") is True

    def test_returns_true_on_301(self):
        with patch("urllib.request.urlopen", return_value=self._mock_resp(301)):
            assert _is_image_reachable("https://example.com/hero.jpg") is True

    def test_returns_false_on_403(self):
        with patch("urllib.request.urlopen", side_effect=Exception("403 Forbidden")):
            assert _is_image_reachable("https://example.com/hero.jpg") is False

    def test_returns_false_on_404(self):
        with patch("urllib.request.urlopen", side_effect=Exception("404 Not Found")):
            assert _is_image_reachable("https://example.com/hero.jpg") is False

    def test_returns_false_on_network_error(self):
        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            assert _is_image_reachable("https://example.com/hero.jpg") is False

    def test_returns_false_on_timeout(self):
        import socket
        with patch("urllib.request.urlopen", side_effect=socket.timeout()):
            assert _is_image_reachable("https://example.com/hero.jpg") is False


class TestHeroImageReachabilityFallback:

    def test_unreachable_image_falls_back_to_gradient(self):
        calls, _ = _run({"images": ["https://testbistro.com/hero.jpg"]}, image_reachable=False)
        prompt = calls[0][1]["messages"][0]["content"]
        assert "NO external image URLs" in prompt

    def test_reachable_image_used_in_prompt(self):
        calls, _ = _run({"images": ["https://testbistro.com/hero.jpg"]}, image_reachable=True)
        prompt = calls[0][1]["messages"][0]["content"]
        assert "https://testbistro.com/hero.jpg" in prompt

    def test_skips_unreachable_picks_next_reachable(self):
        calls, _ = _run(
            {"images": ["https://testbistro.com/hero1.jpg", "https://testbistro.com/hero2.jpg"]},
            image_reachable=lambda url: "hero2" in url,
        )
        prompt = calls[0][1]["messages"][0]["content"]
        assert "hero2.jpg" in prompt
        assert "hero1.jpg" not in prompt

    def test_all_unreachable_uses_gradient(self):
        calls, _ = _run(
            {"images": ["https://testbistro.com/a.jpg", "https://testbistro.com/b.jpg"]},
            image_reachable=False,
        )
        prompt = calls[0][1]["messages"][0]["content"]
        assert "NO external image URLs" in prompt
        assert "linear-gradient" in prompt


# ─── B20: Regression QA — chat, hero image, mobile ───────────────────────────

class TestRegressionQA:
    """Golden-domain regression suite. Any B15-B20 change that breaks these fails here."""

    # ── Chat ──────────────────────────────────────────────────────────────────

    def test_chat_widget_injected_into_final_html(self):
        _, html = _run()
        assert "lvrg-chat-host" in html

    def test_chat_widget_before_body_close(self):
        _, html = _run()
        assert html.index("lvrg-chat-host") < html.index("</body>")

    def test_chat_outside_click_listener_in_final_html(self):
        _, html = _run()
        assert "document.addEventListener('click'" in html

    def test_chat_escape_listener_in_final_html(self):
        _, html = _run()
        assert "e.key==='Escape'" in html

    def test_chat_css_isolation_in_final_html(self):
        _, html = _run()
        # Shadow DOM provides perfect isolation
        assert "attachShadow" in html

    def test_chat_panel_has_mobile_max_width(self):
        _, html = _run()
        assert "max-width:calc(100vw - 32px)" in html

    # ── Hero image ────────────────────────────────────────────────────────────

    def test_hero_image_in_prompt_when_reachable(self):
        calls, _ = _run({"images": ["https://testbistro.com/hero.jpg"]}, image_reachable=True)
        prompt = calls[0][1]["messages"][0]["content"]
        assert "https://testbistro.com/hero.jpg" in prompt

    def test_hero_gradient_in_prompt_when_no_images(self):
        calls, _ = _run({"images": []})
        prompt = calls[0][1]["messages"][0]["content"]
        assert "linear-gradient" in prompt
        assert "NO external image URLs" in prompt

    def test_hero_gradient_fallback_when_image_unreachable(self):
        calls, _ = _run({"images": ["https://testbistro.com/blocked.jpg"]}, image_reachable=False)
        prompt = calls[0][1]["messages"][0]["content"]
        assert "NO external image URLs" in prompt

    # ── Mobile ────────────────────────────────────────────────────────────────

    def test_viewport_meta_instruction_in_part1_prompt(self):
        prompt = _build_part1_prompt(INTEL, "", "gradient", "- no images")
        assert "viewport" in prompt
        assert "width=device-width" in prompt

    def test_mobile_max_width_container_instruction_in_part1_prompt(self):
        prompt = _build_part1_prompt(INTEL, "", "gradient", "- no images")
        assert "max-width" in prompt

    def test_mobile_flex_wrap_instruction_in_part1_prompt(self):
        prompt = _build_part1_prompt(INTEL, "", "gradient", "- no images")
        assert "flex-wrap" in prompt


# ─── B22: </script> injection — intel_json escaping ──────────────────────────

class TestIntelJsonEscaping:

    def _widget(self, intel_override=None):
        from generator import _build_chat_widget
        intel = {**INTEL, **(intel_override or {})}
        return _build_chat_widget(intel)

    def test_script_close_tag_escaped_in_description(self):
        html = self._widget({"description": "We love </script> tags"})
        assert "</script>" not in html.split("<script>", 1)[1].split("</script>")[0]

    def test_escaped_form_present(self):
        html = self._widget({"description": "We love </script> tags"})
        assert "<\\/script>" in html

    def test_script_close_in_business_name_escaped(self):
        html = self._widget({"business_name": "Acme</script>Co"})
        assert "<\\/script>" in html

    def test_script_close_in_social_proof_escaped(self):
        html = self._widget({"social_proof": "Best</script>ever"})
        assert "<\\/script>" in html

    def test_uppercase_script_tag_escaped(self):
        html = self._widget({"description": "Bad </SCRIPT> input"})
        assert "</SCRIPT>" not in html.split("<script>", 1)[1].split("</script>")[0]

    def test_normal_content_not_affected(self):
        html = self._widget({"description": "A great restaurant in San Diego"})
        assert "A great restaurant in San Diego" in html

    def test_other_html_tags_not_mangled(self):
        html = self._widget({"description": "Visit <a href='/'>our site</a>"})
        assert "our site" in html

    def test_widget_js_still_valid_with_escaped_json(self):
        html = self._widget({"description": "Tricky </script> content"})
        # Shadow DOM widget uses local `intel` var inside an IIFE
        assert "var intel=" in html
        assert "history=[]" in html


# ─── Tier 3: content_notes + raw_text slice ──────────────────────────────────

class TestContentNotesPromptInjection:

    def _p1(self, intel_override=None):
        intel = {**INTEL, **(intel_override or {})}
        return _build_part1_prompt(intel, "", "gradient", "- no images")

    def _p2(self, intel_override=None):
        intel = {**INTEL, **(intel_override or {})}
        return _build_part2_prompt(intel)

    def test_content_notes_in_part1_prompt(self):
        prompt = self._p1()
        assert "content_notes" in prompt or "real details" in prompt.lower() or "Tacos al Pastor" in prompt

    def test_content_notes_value_in_part1_prompt(self):
        prompt = self._p1()
        assert "Tacos al Pastor" in prompt

    def test_content_notes_value_in_part2_prompt(self):
        prompt = self._p2()
        assert "Tacos al Pastor" in prompt

    def test_content_notes_fallback_when_empty(self):
        prompt = self._p1({"content_notes": ""})
        assert "Not available" in prompt or "content_notes" in prompt

    def test_part2_copy_rules_include_content_notes(self):
        prompt = self._p2()
        assert "COPY RULES" in prompt
        assert "Tacos al Pastor" in prompt

    def test_different_content_notes_reflected_in_prompts(self):
        notes = "Wagyu Burger: $28, Truffle Fries: $14, Happy Hour Mon-Fri 3-6pm"
        p1 = self._p1({"content_notes": notes})
        p2 = self._p2({"content_notes": notes})
        assert "Wagyu Burger" in p1
        assert "Wagyu Burger" in p2

    def test_raw_text_sliced_at_3500_in_part1_prompt(self):
        long_raw = "X" * 5000
        prompt = self._p1({"raw_text": long_raw})
        # The 3500 X's should be in the prompt; anything past 3500 should not
        assert "X" * 3500 in prompt
        assert "X" * 3501 not in prompt

    def test_raw_text_not_truncated_at_2000_in_part1_prompt(self):
        """Verify the old [:2000] slice is gone — 2001 chars should appear."""
        raw = "Y" * 3000
        prompt = self._p1({"raw_text": raw})
        assert "Y" * 2001 in prompt


# ─── Footer — Python-generated, always present ───────────────────────────────

class TestFooter:

    def _footer(self, intel_override=None):
        intel = {**INTEL, **(intel_override or {})}
        return _build_footer(intel)

    def test_footer_contains_business_name(self):
        assert "Test Bistro" in self._footer()

    def test_footer_contains_location(self):
        assert "Gaslamp, San Diego, CA" in self._footer()

    def test_footer_contains_phone(self):
        assert "619-555-0000" in self._footer()

    def test_footer_contains_hours(self):
        assert "Mon-Sun 11am-10pm" in self._footer()

    def test_footer_contains_cta_angle(self):
        assert "Reserve a Table" in self._footer()

    def test_footer_uses_primary_color_as_background(self):
        assert "background:#1a1a2e" in self._footer()

    def test_footer_uses_secondary_color_for_headings(self):
        assert "#c9a961" in self._footer()

    def test_footer_has_copyright_line(self):
        assert "© 2025" in self._footer()

    def test_footer_contains_lvrg_agency_link(self):
        assert "LVRG Agency" in self._footer()

    def test_footer_phone_is_a_tel_link(self):
        assert 'href="tel:619-555-0000"' in self._footer()

    def test_footer_email_is_a_mailto_link(self):
        assert 'href="mailto:hi@testbistro.com"' in self._footer()

    def test_footer_missing_phone_does_not_add_empty_tel_link(self):
        footer = self._footer({"phone": ""})
        assert 'href="tel:"' not in footer

    def test_footer_missing_email_does_not_add_empty_mailto_link(self):
        footer = self._footer({"email": ""})
        assert 'href="mailto:"' not in footer

    def test_footer_missing_hours_shows_fallback(self):
        footer = self._footer({"hours": ""})
        assert "Call for hours" in footer

    def test_footer_contains_description_snippet(self):
        assert "A great restaurant" in self._footer()

    def test_footer_injected_in_final_html(self):
        _, html = _run()
        assert "<footer" in html

    def test_footer_appears_before_chat_widget(self):
        _, html = _run()
        assert html.index("<footer") < html.index("lvrg-chat-host")

    def test_footer_appears_before_body_close(self):
        _, html = _run()
        assert html.index("<footer") < html.index("</body>")

    def test_footer_present_even_when_part2_truncated(self):
        _, html = _run(part2="<section>TESTIMONIALS ONLY</section>")
        assert "<footer" in html

    def test_html_closes_after_chat_widget(self):
        _, html = _run()
        assert html.rstrip().endswith("</html>")


# ─── Chat widget: SVG icon + animation (Shadow DOM) ──────────────────────────

class TestChatWidgetSvgAndAnimation:

    def test_chat_icon_svg_present_with_stroke(self):
        _, html = _run()
        # SVG markup is embedded as a JSON-escaped JS string literal — quotes appear as \"
        assert 'stroke=\\"#ffffff\\"' in html

    def test_send_icon_svg_present_with_fill(self):
        _, html = _run()
        assert 'fill=\\"#ffffff\\"' in html

    def test_chat_icon_svg_actually_renders_white(self):
        # Quick sanity check: when the JS string is evaluated, the attribute would be
        # stroke="#ffffff" (the JSON layer just escapes the quotes for the source file).
        _, html = _run()
        # The hex itself is in the file regardless of quoting style
        assert "#ffffff" in html

    def test_panel_uses_opacity_for_hidden_state(self):
        _, html = _run()
        # Panel starts hidden via opacity:0 in Shadow DOM stylesheet
        assert "opacity:0" in html

    def test_panel_no_display_none_anywhere(self):
        _, html = _run()
        # display:none can hide elements but breaks transitions; widget should not use it
        assert "display:none" not in html

    def test_panel_has_smooth_cubic_bezier_transition(self):
        _, html = _run()
        assert "cubic-bezier" in html

    def test_panel_open_class_drives_animation(self):
        _, html = _run()
        # Opening adds a .open class which transitions opacity + transform
        assert ".panel.open" in html
        assert "panel.classList.add('open')" in html

    def test_open_class_sets_full_opacity(self):
        _, html = _run()
        assert "opacity:1;transform:translateY(0) scale(1)" in html

    def test_close_removes_open_class(self):
        _, html = _run()
        assert "panel.classList.remove('open')" in html

    def test_widget_button_has_hover_scale(self):
        _, html = _run()
        assert ".btn:hover{transform:scale" in html


# ─── _clean_html: SVG data URL encoding ──────────────────────────────────────

class TestCleanHtml:

    def test_encodes_double_quotes_inside_svg_data_url(self):
        raw = """<div style="background:url('data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg"><circle/></svg>');opacity:0.5;">"""
        result = _clean_html(raw)
        # The " inside the data URL must be replaced with %22
        assert '%22http://www.w3.org/2000/svg%22' in result

    def test_does_not_encode_quotes_outside_data_url(self):
        raw = """<div style="color:#fff;background:url('data:image/svg+xml,<svg xmlns="x"/>');padding:10px;">"""
        result = _clean_html(raw)
        # The outer style attribute quotes should remain intact
        assert 'style="color:#fff' in result

    def test_plain_html_unchanged(self):
        html = "<div><p>Hello world</p></div>"
        assert _clean_html(html) == html

    def test_non_svg_data_urls_unchanged(self):
        html = """<img src="data:image/png;base64,abc123">"""
        assert _clean_html(html) == html

    def test_multiple_svg_data_urls_all_fixed(self):
        raw = (
            """<div style="background:url('data:image/svg+xml,<svg xmlns="a"/>');"></div>"""
            """<div style="background:url('data:image/svg+xml,<svg xmlns="b"/>');"></div>"""
        )
        result = _clean_html(raw)
        assert result.count('%22') >= 2


# ─── _close_unclosed_elements ─────────────────────────────────────────────────

class TestCloseUnclosedElements:

    def test_closes_unclosed_textarea(self):
        html = '<section><textarea placeholder="Message">'
        result = _close_unclosed_elements(html)
        assert '</textarea>' in result

    def test_does_not_double_close_properly_closed_textarea(self):
        html = '<textarea>Hello</textarea>'
        result = _close_unclosed_elements(html)
        assert result.count('</textarea>') == 1

    def test_closes_unclosed_select(self):
        html = '<select><option>A</option>'
        result = _close_unclosed_elements(html)
        assert '</select>' in result

    def test_widget_not_swallowed_by_unclosed_textarea(self):
        # Simulate Claude leaving a <textarea> open, then we inject footer + widget
        part2_with_open_textarea = PART2_HTML.replace(
            "<section>CTA BANNER</section>",
            "<section><textarea placeholder='Your message'>"
        )
        _, html = _run(part2=part2_with_open_textarea)
        # Chat widget host must appear in the DOM, not as text content of a textarea
        assert 'id="lvrg-chat-host"' in html
        # Footer must also be present
        assert '<footer' in html

    def test_plain_html_unchanged(self):
        html = "<div><p>Hello</p></div>"
        result = _close_unclosed_elements(html)
        assert result.strip() == html


# ─── _is_light_color ─────────────────────────────────────────────────────────

class TestIsLightColor:

    def test_white_is_light(self):
        assert _is_light_color('#ffffff') is True

    def test_fff_shorthand_not_handled_returns_false(self):
        # Only full 6-char hex supported; 3-char returns False (safe default)
        assert _is_light_color('#fff') is False

    def test_black_is_not_light(self):
        assert _is_light_color('#000000') is False

    def test_dark_navy_is_not_light(self):
        assert _is_light_color('#1a1a2e') is False

    def test_gold_is_not_light(self):
        assert _is_light_color('#c9a961') is False

    def test_near_white_is_light(self):
        assert _is_light_color('#f0f0f0') is True

    def test_medium_gray_is_not_light(self):
        assert _is_light_color('#888888') is False


# ─── Footer CTA color — falls back when secondary is light ───────────────────

class TestFooterCtaColor:

    def test_white_secondary_uses_primary_for_cta(self):
        intel = {**INTEL, "secondary_color": "#ffffff", "primary_color": "#DC143C"}
        footer = _build_footer(intel)
        # Button should use primary (#DC143C), not secondary (#ffffff)
        assert "background:#DC143C" in footer

    def test_light_secondary_does_not_produce_white_button(self):
        intel = {**INTEL, "secondary_color": "#f5f5f5", "primary_color": "#2c3e50"}
        footer = _build_footer(intel)
        assert "background:#f5f5f5" not in footer
        assert "background:#2c3e50" in footer

    def test_dark_secondary_is_used_as_cta_bg(self):
        intel = {**INTEL, "secondary_color": "#c9a961", "primary_color": "#1a1a2e"}
        footer = _build_footer(intel)
        assert "background:#c9a961" in footer

    def test_cta_button_always_has_white_text(self):
        intel_light = {**INTEL, "secondary_color": "#ffffff"}
        intel_dark = {**INTEL, "secondary_color": "#c9a961"}
        for intel in (intel_light, intel_dark):
            assert "color:#fff" in _build_footer(intel)


# ─── Part 1 dividers ban ─────────────────────────────────────────────────────

class TestPart1NoDividers:

    def _p1(self):
        return _build_part1_prompt(INTEL, "", "gradient", "- no images")

    def test_prompt_explicitly_bans_dividers_between_top_sections(self):
        prompt = self._p1()
        assert "NO divider" in prompt or "no divider" in prompt.lower()

    def test_prompt_mentions_seamless_flow(self):
        prompt = self._p1()
        assert "seamless" in prompt.lower() or "flow" in prompt.lower()

    def test_prompt_bans_hr_element_between_top_sections(self):
        prompt = self._p1()
        assert "<hr>" in prompt


# ─── Part 2 — real Google reviews ────────────────────────────────────────────

REVIEWS = [
    {"author": "Sarah M.", "rating": 5, "text": "Best burgers in Ocean Beach hands down. The ambiance is unmatched.", "time_ago": "2 weeks ago"},
    {"author": "Mike R.", "rating": 5, "text": "Worth the wait. Great staff, killer fries.", "time_ago": "1 month ago"},
    {"author": "Lisa K.", "rating": 4, "text": "Solid spot. Get the bacon cheeseburger.", "time_ago": "3 weeks ago"},
]


class TestPart2WithRealReviews:

    def test_reviews_appear_verbatim_in_prompt(self):
        intel = {**INTEL, "reviews": REVIEWS}
        prompt = _build_part2_prompt(intel)
        assert "Best burgers in Ocean Beach" in prompt
        assert "Worth the wait" in prompt

    def test_review_authors_appear_in_prompt(self):
        intel = {**INTEL, "reviews": REVIEWS}
        prompt = _build_part2_prompt(intel)
        assert "Sarah M." in prompt
        assert "Mike R." in prompt

    def test_review_ratings_appear_in_prompt(self):
        intel = {**INTEL, "reviews": REVIEWS}
        prompt = _build_part2_prompt(intel)
        assert "5★" in prompt or "5★" in prompt

    def test_prompt_instructs_verbatim_no_paraphrasing(self):
        intel = {**INTEL, "reviews": REVIEWS}
        prompt = _build_part2_prompt(intel)
        assert "verbatim" in prompt.lower()
        assert "paraphrase" in prompt.lower() or "DO NOT" in prompt

    def test_reviews_with_empty_text_filtered_out(self):
        reviews = [
            {"author": "A", "rating": 5, "text": "", "time_ago": ""},
            {"author": "B", "rating": 5, "text": "Real review", "time_ago": ""},
        ]
        intel = {**INTEL, "reviews": reviews}
        prompt = _build_part2_prompt(intel)
        assert "Real review" in prompt

    def test_prompt_caps_at_3_reviews(self):
        many = [
            {"author": f"User{i}", "rating": 5, "text": f"Review number {i} content", "time_ago": ""}
            for i in range(10)
        ]
        intel = {**INTEL, "reviews": many}
        prompt = _build_part2_prompt(intel)
        # Review 0, 1, 2 included; 3+ excluded
        assert "Review number 0" in prompt
        assert "Review number 2" in prompt
        assert "Review number 3" not in prompt

    def test_falls_back_to_social_proof_when_no_reviews(self):
        intel = {**INTEL, "reviews": []}
        prompt = _build_part2_prompt(intel)
        # Should reference social_proof field (fallback path)
        assert "Best of SD 2023" in prompt

    def test_falls_back_to_social_proof_when_reviews_missing(self):
        intel = {k: v for k, v in INTEL.items() if k != "reviews"}
        prompt = _build_part2_prompt(intel)
        assert "Best of SD 2023" in prompt


# ─── Chat widget: raw_text included (truncated) ──────────────────────────────

class TestChatWidgetRichContext:

    def test_raw_text_now_in_widget_intel(self):
        # Previously raw_text was excluded; now it's included (capped at 2500 chars)
        from generator import _build_chat_widget
        intel = {**INTEL, "raw_text": "We serve the best handcrafted burgers in San Diego since 1969."}
        html = _build_chat_widget(intel)
        assert "best handcrafted burgers" in html

    def test_raw_text_capped_at_2500_chars(self):
        from generator import _build_chat_widget
        long_text = "A" * 5000
        intel = {**INTEL, "raw_text": long_text}
        html = _build_chat_widget(intel)
        # The full 5000-char string should not be in the widget
        assert "A" * 5000 not in html
        # But 2500 chars should be present
        assert "A" * 2500 in html

    def test_reviews_included_in_widget_intel_json(self):
        from generator import _build_chat_widget
        intel = {**INTEL, "reviews": REVIEWS}
        html = _build_chat_widget(intel)
        assert "Sarah M." in html
        assert "Best burgers in Ocean Beach" in html

    def test_google_rating_included_in_widget_intel_json(self):
        from generator import _build_chat_widget
        intel = {**INTEL, "google_rating": 4.7, "google_total_ratings": 250}
        html = _build_chat_widget(intel)
        assert "4.7" in html
        assert "250" in html
