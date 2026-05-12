"""
LVRG Engine — Design Variation System.

Two generated sites used to look ~90% identical because the prompt baked in a
single rigid layout (claim-bar → nav → hero → social proof → services → testimonials → CTA).
This module produces a per-prospect DesignSpec that drives:

  • personality       (mood, density, button style, card style, shadow scale)
  • palette           (bg, surface, text, border, accent — derived from intel primary_color)
  • font pairing      (heading + body Google Fonts matched to personality)
  • section variants  (5 hero, 4 nav, 4 social-proof, 5 services, 4 CTA layouts)
  • section order     (controlled shuffle of Pass 1 sections)
  • CSS tokens        (border-radius, shadow, section-padding)

The spec is consumed in two places inside generator.py:

  1. render_css_tokens(spec) → <style> block injected into <head>. CSS custom properties
     are GUARANTEED to apply regardless of Claude's HTML output, so palette + fonts
     stay consistent.

  2. render_prompt_block(spec, "part1" | "part2") → a "DESIGN VARIATION" block
     prepended to the Claude prompt. Tells Claude which hero/nav/services/CTA layout
     to build and which CSS vars to reference.

Determinism: the same domain always gets the same design (seed from sha256(domain)).
Useful for: (a) test stability, (b) user re-generating a prospect's preview gets the
same look rather than flickering between designs.
"""

import hashlib
import random
from typing import Optional


# ───────────────────────────────────────────────────────────────────────────
# 1. PERSONALITIES — broad design moods. Each governs density, type weight,
#    button + card styling, shadow intensity, and section-y spacing.
#    `industry_weights` tilts the weighted-random pick toward fits.
# ───────────────────────────────────────────────────────────────────────────

PERSONALITIES = {
    "minimal": {
        "mood": "Quiet and restrained. Lots of whitespace. Type does the work. Zero decoration.",
        "spacing_section_y": "120px",
        "type_weight_heading": 600,
        "type_weight_body": 400,
        "radius_md": "8px",
        "radius_lg": "12px",
        "shadow_intensity": "subtle",
        "card_style": "borderless",
        "button_style": "outline",
        "section_density": "airy",
        "force_dark_mode": False,
        "industry_weights": {
            "professional": 3, "wellness": 3, "law": 3, "tech": 2,
            "default": 1,
        },
    },
    "modern_saas": {
        "mood": "Clean, friendly, conversion-optimised. Soft shadows, generous radii, gradient touches.",
        "spacing_section_y": "96px",
        "type_weight_heading": 700,
        "type_weight_body": 400,
        "radius_md": "12px",
        "radius_lg": "20px",
        "shadow_intensity": "medium",
        "card_style": "elevated",
        "button_style": "filled",
        "section_density": "medium",
        "force_dark_mode": False,
        "industry_weights": {
            "tech": 4, "professional": 2, "retail": 2, "catering": 2,
            "default": 1,
        },
    },
    "corporate": {
        "mood": "Trustworthy, structured, formal. Grid-aligned. Conservative.",
        "spacing_section_y": "88px",
        "type_weight_heading": 700,
        "type_weight_body": 400,
        "radius_md": "6px",
        "radius_lg": "10px",
        "shadow_intensity": "subtle",
        "card_style": "bordered",
        "button_style": "filled",
        "section_density": "medium",
        "force_dark_mode": False,
        "industry_weights": {
            "law": 4, "finance": 4, "professional": 3, "catering": 2,
            "default": 1,
        },
    },
    "dark_premium": {
        "mood": "Atmospheric, moody, premium. Near-black backgrounds, single jewel-tone accent.",
        "spacing_section_y": "104px",
        "type_weight_heading": 700,
        "type_weight_body": 400,
        "radius_md": "10px",
        "radius_lg": "18px",
        "shadow_intensity": "dramatic",
        "card_style": "tinted",
        "button_style": "gradient",
        "section_density": "medium",
        "force_dark_mode": True,
        "industry_weights": {
            "bar": 4, "restaurant": 3, "craft_beverage": 3, "luxury": 4,
            "default": 1,
        },
    },
    "editorial": {
        "mood": "Magazine layout, large serif headings, asymmetric grid, mixed column widths.",
        "spacing_section_y": "96px",
        "type_weight_heading": 700,
        "type_weight_body": 400,
        "radius_md": "0px",
        "radius_lg": "0px",
        "shadow_intensity": "none",
        "card_style": "borderless",
        "button_style": "underline",
        "section_density": "medium",
        "force_dark_mode": False,
        "industry_weights": {
            "restaurant": 3, "coffee_shop": 3, "creative": 4, "fashion": 4,
            "default": 1,
        },
    },
    "bento": {
        "mood": "Asymmetric bento-grid sections, mixed card sizes, playful but structured.",
        "spacing_section_y": "88px",
        "type_weight_heading": 700,
        "type_weight_body": 400,
        "radius_md": "20px",
        "radius_lg": "32px",
        "shadow_intensity": "medium",
        "card_style": "elevated",
        "button_style": "filled",
        "section_density": "medium",
        "force_dark_mode": False,
        "industry_weights": {
            "tech": 3, "creative": 4, "retail": 3, "coffee_shop": 2,
            "default": 1,
        },
    },
    "gradient_modern": {
        "mood": "Bold gradient backgrounds, soft glows, vibrant. Slight glassmorphism on cards.",
        "spacing_section_y": "96px",
        "type_weight_heading": 800,
        "type_weight_body": 400,
        "radius_md": "16px",
        "radius_lg": "28px",
        "shadow_intensity": "dramatic",
        "card_style": "elevated",
        "button_style": "gradient",
        "section_density": "medium",
        "force_dark_mode": False,
        "industry_weights": {
            "tech": 3, "creative": 3, "fitness": 3, "craft_beverage": 2,
            "default": 1,
        },
    },
    "warm_artisan": {
        "mood": "Earthy palette (terracotta, olive, cream). Slab serif headings. Handcrafted feel.",
        "spacing_section_y": "96px",
        "type_weight_heading": 700,
        "type_weight_body": 400,
        "radius_md": "14px",
        "radius_lg": "24px",
        "shadow_intensity": "subtle",
        "card_style": "tinted",
        "button_style": "filled",
        "section_density": "medium",
        "force_dark_mode": False,
        "industry_weights": {
            "coffee_shop": 4, "restaurant": 3, "wellness": 3, "retail": 2,
            "default": 1,
        },
    },
}


# ───────────────────────────────────────────────────────────────────────────
# 2. FONT PAIRINGS — Google Fonts (preconnect/preload handled in CSS block)
#    `tags` lists personalities this pairing suits; picker filters by tag.
# ───────────────────────────────────────────────────────────────────────────

FONT_PAIRINGS = [
    {"heading": "Space Grotesk",      "body": "Inter",           "tags": ["modern_saas", "tech", "bento", "gradient_modern"]},
    {"heading": "Sora",               "body": "Inter",           "tags": ["modern_saas", "minimal"]},
    {"heading": "Bricolage Grotesque","body": "DM Sans",         "tags": ["bento", "modern_saas", "gradient_modern"]},
    {"heading": "Manrope",            "body": "Manrope",         "tags": ["minimal", "modern_saas"]},
    {"heading": "Plus Jakarta Sans",  "body": "Inter",           "tags": ["modern_saas", "corporate"]},
    {"heading": "Outfit",             "body": "Inter",           "tags": ["modern_saas", "gradient_modern", "bento"]},
    {"heading": "IBM Plex Sans",      "body": "IBM Plex Sans",   "tags": ["corporate", "minimal"]},
    {"heading": "Inter Tight",        "body": "Inter",           "tags": ["minimal", "corporate", "modern_saas"]},
    {"heading": "Playfair Display",   "body": "Lato",            "tags": ["editorial", "dark_premium", "warm_artisan"]},
    {"heading": "Fraunces",           "body": "Inter",           "tags": ["editorial", "warm_artisan", "minimal"]},
    {"heading": "Cormorant Garamond", "body": "Inter",           "tags": ["editorial", "dark_premium", "minimal"]},
    {"heading": "DM Serif Display",   "body": "DM Sans",         "tags": ["editorial", "warm_artisan"]},
    {"heading": "Lora",               "body": "Inter",           "tags": ["warm_artisan", "editorial"]},
    {"heading": "Recoleta",           "body": "DM Sans",         "tags": ["warm_artisan", "editorial"]},
    {"heading": "Syne",               "body": "Inter",           "tags": ["bento", "gradient_modern", "editorial"]},
    {"heading": "Instrument Serif",   "body": "Inter",           "tags": ["editorial", "dark_premium"]},
]


# ───────────────────────────────────────────────────────────────────────────
# 3. SECTION VARIANTS — explicit layout descriptions for each section type.
#    Each variant carries `weight` (random-pick tilt) and an `image_usage`
#    hint that downstream code uses to compose the right hero-bg instruction.
# ───────────────────────────────────────────────────────────────────────────

# image_usage values:
#   "background_overlay" — image is full-bleed bg with dark overlay
#   "inline_column"      — image is an <img> inside a flex/grid column
#   "no_image"           — variant doesn't use any image at all

HERO_VARIANTS = [
    {
        "id": "centered_overlay",
        "spec": "Full-bleed background image (or gradient) with a soft dark overlay for readability. Centered headline + 1-line subtext + 2 CTAs side-by-side. Min-height 70vh.",
        "image_usage": "background_overlay",
        "weight": 2,
    },
    {
        "id": "split_text_left_visual_right",
        "spec": "Two-column 55/45 grid. LEFT column: small uppercase eyebrow tag, then 5-8 word heading (largest type), then 1-2 sentence subhead, then 2 CTA buttons. RIGHT column: large rounded-corner image (use the hero image URL as a regular <img>, NOT a background). Stack to single column under 768px.",
        "image_usage": "inline_column",
        "weight": 3,
    },
    {
        "id": "stacked_badge_minimal",
        "spec": "Centered single column. Tiny uppercase eyebrow pill at top. Massive display heading (very large, tight line-height). Short 1-line subhead. ONE primary CTA. No background image. Use a flat surface colour or very subtle linear-gradient mesh. Lots of vertical whitespace.",
        "image_usage": "no_image",
        "weight": 2,
    },
    {
        "id": "magazine_overlay_card",
        "spec": "Large hero image fills the section. A solid card (use the surface colour) sits as an overlay on the bottom-left third of the image, offset inward, containing heading + short subhead + single CTA. Editorial mood. Card has var(--lvrg-radius-lg) border-radius and a strong shadow.",
        "image_usage": "background_overlay",
        "weight": 1,
    },
    {
        "id": "asymmetric_offset_text",
        "spec": "Heading + subtext sit in the left 55% column. Right 45% is a tall accent-coloured block with the hero photo as an <img> floating on top of it, offset down-and-right by ~24px. Two CTA buttons sit side-by-side beneath the left column.",
        "image_usage": "inline_column",
        "weight": 1,
    },
    {
        "id": "stat_anchored",
        "spec": "Hero text occupies the TOP half (heading + 1-line subhead + single CTA, all centred). BOTTOM half is a horizontal row of 3-4 large stat blocks (big number + small label below). Stats are sourced from the real social-proof intel. No background image — flat surface or subtle gradient.",
        "image_usage": "no_image",
        "weight": 1,
    },
]

NAV_VARIANTS = [
    {
        "id": "logo_left_links_centre_cta_right",
        "spec": "Standard top nav: logo on the left, 3-4 nav links centred, primary CTA button on the right. Sticky with subtle backdrop-blur on scroll.",
        "weight": 3,
    },
    {
        "id": "logo_left_cta_right_no_links",
        "spec": "Minimal nav: logo on the left, single CTA button on the right. No middle links at all. Sticky.",
        "weight": 1,
    },
    {
        "id": "centred_logo_links_below",
        "spec": "Logo centred at the very top. 4 nav links sit in a single row centred below the logo. The primary CTA button sits at the far right of the link row.",
        "weight": 1,
    },
    {
        "id": "transparent_overlay",
        "spec": "Nav is transparent and floats over the hero. Use white text and white logo if hero is dark. Sticky with the background becoming opaque on scroll. Logo left, links + CTA right.",
        "weight": 1,
    },
]

SOCIAL_PROOF_VARIANTS = [
    {
        "id": "horizontal_stat_strip",
        "spec": "Single horizontal row of 3-4 large stat numbers with small labels underneath. Generous gap between stats. Centred on screen. Use surface-alt as background.",
        "weight": 2,
    },
    {
        "id": "rating_pill_centred",
        "spec": "A single centred pill containing rating stars + average rating + total reviews + a one-line trust phrase. Compact, single line on desktop, stacks on mobile.",
        "weight": 2,
    },
    {
        "id": "logo_strip_with_quote",
        "spec": "Top: small uppercase 'As featured in' eyebrow + a row of press source name badges (text-only, no images). Below: one short pull-quote attribution from a press mention. Skip cleanly if no press data.",
        "weight": 1,
    },
    {
        "id": "split_rating_and_stats",
        "spec": "Two-column row. LEFT column: rating star block + review count. RIGHT column: 2 stats (years open + customers served or similar). Subtle vertical divider between columns.",
        "weight": 1,
    },
]

SERVICES_VARIANTS = [
    {
        "id": "card_grid_3col",
        "spec": "3 equal cards in a horizontal grid. Each card: icon or numeric counter, service name as h3, 1-2 sentence description, no CTA. Cards inherit the personality's card style (elevated / bordered / tinted / borderless).",
        "weight": 2,
    },
    {
        "id": "alternating_split_rows",
        "spec": "Each service is rendered as a full-width row alternating image LEFT / content RIGHT, then content LEFT / image RIGHT, then image LEFT / content RIGHT. NO cards, NO grid. Generous vertical padding between rows.",
        "weight": 1,
    },
    {
        "id": "bento_asymmetric",
        "spec": "Bento grid of mixed-size cells. Total of 5 cells: one wide hero cell (the most important service), then 2 medium cells, then 2 small cells. Each cell still contains a service name + short description. Cells have the personality's card style.",
        "weight": 1,
    },
    {
        "id": "vertical_feature_list",
        "spec": "Vertical numbered list. Each row: large display-style number (01, 02, 03) in accent colour on the LEFT, service name + 1 sentence description on the RIGHT. Subtle 1px border-bottom (NOT a section divider) between rows.",
        "weight": 1,
    },
    {
        "id": "icon_row_horizontal_compact",
        "spec": "Single horizontal row of 3-5 compact icon-above-label items. No card backgrounds, no descriptions, just an icon and the service name as the label. Centred. Compact, low visual weight.",
        "weight": 1,
    },
]

CTA_VARIANTS = [
    {
        "id": "centred_full_width_band",
        "spec": "Full-width section with the accent colour as background. Centred heading + 1-sentence subcopy + single large CTA button. Generous vertical padding.",
        "weight": 2,
    },
    {
        "id": "split_card_with_visual",
        "spec": "Centred card (max-width 960px). LEFT half: an accent-coloured visual block. RIGHT half: heading + subcopy + CTA button. Card sits on a surface-alt page background.",
        "weight": 1,
    },
    {
        "id": "stacked_huge_heading",
        "spec": "Centred. No background colour change (or very subtle surface tint). MASSIVE display heading (the biggest type on the entire page). Single short CTA button below. Lots of vertical space above and below.",
        "weight": 1,
    },
    {
        "id": "tinted_card_centred",
        "spec": "A tinted card centred on the page using var(--lvrg-radius-lg). Inside: small accent-coloured eyebrow tag + heading + 1-line subcopy + single CTA. Surrounded by whitespace, page background stays neutral.",
        "weight": 1,
    },
]

# Pass-1 section orderings. Claim-bar + nav are always first.
# Testimonials and CTA are injected after Pass 1 (testimonials via Python, CTA via Pass 2).
SECTION_ORDERS_PART1 = [
    ["hero", "social_proof", "services"],
    ["hero", "services", "social_proof"],
    ["hero", "social_proof", "services"],
    ["hero", "social_proof", "services"],
    ["hero", "services", "social_proof"],
]


# ───────────────────────────────────────────────────────────────────────────
# 4. COLOUR UTILITIES — derive a full palette from a single brand colour.
# ───────────────────────────────────────────────────────────────────────────

def _hex_to_rgb(h: str):
    h = (h or "").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return None


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{max(0,min(255,r)):02x}{max(0,min(255,g)):02x}{max(0,min(255,b)):02x}"


def _relative_luminance(h: str) -> float:
    rgb = _hex_to_rgb(h)
    if not rgb:
        return 0.5
    r, g, b = rgb
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255


def _is_light(h: str, threshold: float = 0.7) -> bool:
    return _relative_luminance(h) > threshold


def _mix(h1: str, h2: str, t: float) -> str:
    """Linear-mix two hex colours, t=0 → h1, t=1 → h2. Returns h1 on parse failure."""
    a, b = _hex_to_rgb(h1), _hex_to_rgb(h2)
    if not a or not b:
        return h1
    return _rgb_to_hex(
        int(a[0] + (b[0] - a[0]) * t),
        int(a[1] + (b[1] - a[1]) * t),
        int(a[2] + (b[2] - a[2]) * t),
    )


def _derive_palette(primary: str, secondary: str, force_dark: bool) -> dict:
    """Build a full UI palette around the prospect's brand primary.

    Light mode: white bg, slate text, primary becomes the accent.
    Dark mode:  near-black bg, off-white text, accent picked to read on dark.
    If `primary` itself is very light, we use it as accent on a dark mode scheme
    so it remains visible (a pale-yellow brand would disappear on a white page).
    """
    primary = primary or "#1a1a2e"
    secondary = secondary or "#c9a961"
    p_light = _is_light(primary)
    mode = "dark" if (force_dark or p_light) else "light"
    accent = primary if not (mode == "dark" and p_light) else primary  # keep primary as accent

    if mode == "light":
        accent_contrast = "#ffffff" if _relative_luminance(accent) < 0.6 else "#0f172a"
        return {
            "mode": "light",
            "bg": "#ffffff",
            "surface": "#f8fafc",
            "surface_alt": "#f1f5f9",
            "text_primary": "#0f172a",
            "text_secondary": "#475569",
            "text_muted": "#94a3b8",
            "border": "#e2e8f0",
            "accent": accent,
            "accent_dark": _mix(accent, "#000000", 0.18),
            "accent_soft": _mix(accent, "#ffffff", 0.88),
            "accent_contrast": accent_contrast,
        }

    # dark mode — accent must read against #0a0a0a. If the brand primary is
    # itself near-black, fall back to secondary; if that's also too dark,
    # lighten the primary so CTAs and key highlights remain visible.
    if _relative_luminance(accent) < 0.20:
        if _relative_luminance(secondary) > 0.30:
            accent = secondary
        else:
            accent = _mix(accent, "#ffffff", 0.55)
    accent_contrast = "#0a0a0a" if _is_light(accent) else "#ffffff"
    return {
        "mode": "dark",
        "bg": "#0a0a0a",
        "surface": "#141414",
        "surface_alt": "#1c1c1c",
        "text_primary": "#f8fafc",
        "text_secondary": "#cbd5e1",
        "text_muted": "#64748b",
        "border": "#27272a",
        "accent": accent,
        "accent_dark": _mix(accent, "#000000", 0.22),
        "accent_soft": _mix(accent, "#0a0a0a", 0.78),
        "accent_contrast": accent_contrast,
    }


def _shadow_for(intensity: str, large: bool = False) -> str:
    table = {
        "none":     ("none", "none"),
        "subtle":   ("0 4px 16px rgba(0,0,0,0.06)",  "0 16px 48px rgba(0,0,0,0.08)"),
        "medium":   ("0 4px 24px rgba(0,0,0,0.10)",  "0 20px 60px rgba(0,0,0,0.14)"),
        "dramatic": ("0 8px 40px rgba(0,0,0,0.20)",  "0 32px 80px rgba(0,0,0,0.32)"),
    }
    pair = table.get(intensity, table["medium"])
    return pair[1] if large else pair[0]


# ───────────────────────────────────────────────────────────────────────────
# 5. WEIGHTED PICKERS — seeded RNG keeps the same domain stable across re-runs.
# ───────────────────────────────────────────────────────────────────────────

def _seeded_rng(intel: dict, seed_override: Optional[int] = None) -> random.Random:
    if seed_override is not None:
        return random.Random(seed_override)
    seed_str = (intel.get("domain") or intel.get("business_name") or "lvrg").lower().strip()
    digest = hashlib.sha256(seed_str.encode("utf-8")).digest()
    seed = int.from_bytes(digest[:8], "big")
    return random.Random(seed)


def _weighted_pick(rng: random.Random, items: list) -> dict:
    weights = [max(1, int(item.get("weight", 1))) for item in items]
    return rng.choices(items, weights=weights, k=1)[0]


def _pick_personality(rng: random.Random, business_type: str):
    names = list(PERSONALITIES.keys())
    weights = []
    for n in names:
        w = PERSONALITIES[n]["industry_weights"].get(
            business_type,
            PERSONALITIES[n]["industry_weights"].get("default", 1),
        )
        weights.append(max(1, int(w)))
    chosen = rng.choices(names, weights=weights, k=1)[0]
    return chosen, PERSONALITIES[chosen]


def _pick_fonts(rng: random.Random, personality_name: str) -> dict:
    candidates = [f for f in FONT_PAIRINGS if personality_name in f["tags"]]
    if not candidates:
        candidates = FONT_PAIRINGS  # fall back to any pairing
    return rng.choice(candidates)


def _pick_hero(rng: random.Random, has_reachable_image: bool) -> dict:
    """When a reachable hero image is available, restrict to variants that
    actually USE the image (otherwise we'd waste the photo + the existing tests
    that check 'image URL appears in prompt' would fail). When no image is
    available, restrict to no_image variants."""
    if has_reachable_image:
        valid = [v for v in HERO_VARIANTS if v["image_usage"] != "no_image"]
    else:
        valid = [v for v in HERO_VARIANTS if v["image_usage"] == "no_image"]
    return _weighted_pick(rng, valid)


# ───────────────────────────────────────────────────────────────────────────
# 6. compose_design — the only public entry point used by generator.py
# ───────────────────────────────────────────────────────────────────────────

def compose_design(
    intel: dict,
    *,
    has_reachable_image: bool = False,
    seed_override: Optional[int] = None,
) -> dict:
    """Return a DesignSpec dict assembled from the intel.

    has_reachable_image: pass True only if generator.py has already verified
      at least one hero image responds 2xx/3xx (HEAD). Variants that require an
      image are filtered out otherwise so we never reference a broken URL.

    seed_override: forces a specific RNG seed (used by tests for determinism /
      variance assertions). Omit in production — the domain seeds it.
    """
    rng = _seeded_rng(intel, seed_override)

    business_type = (intel.get("business_type") or "other").lower()
    primary = intel.get("primary_color", "#1a1a2e")
    secondary = intel.get("secondary_color", "#c9a961")

    personality_name, personality = _pick_personality(rng, business_type)
    fonts = _pick_fonts(rng, personality_name)
    palette = _derive_palette(primary, secondary, force_dark=personality["force_dark_mode"])

    hero = _pick_hero(rng, has_reachable_image)
    nav = _weighted_pick(rng, NAV_VARIANTS)
    social = _weighted_pick(rng, SOCIAL_PROOF_VARIANTS)
    services = _weighted_pick(rng, SERVICES_VARIANTS)
    cta = _weighted_pick(rng, CTA_VARIANTS)

    section_order = rng.choice(SECTION_ORDERS_PART1)

    tokens = {
        "section_y": personality["spacing_section_y"],
        "radius_md": personality["radius_md"],
        "radius_lg": personality["radius_lg"],
        "shadow_md": _shadow_for(personality["shadow_intensity"], large=False),
        "shadow_lg": _shadow_for(personality["shadow_intensity"], large=True),
        "type_weight_heading": personality["type_weight_heading"],
        "type_weight_body": personality["type_weight_body"],
    }

    return {
        "personality": personality_name,
        "personality_data": personality,
        "fonts": fonts,
        "palette": palette,
        "variants": {
            "hero": hero,
            "nav": nav,
            "social_proof": social,
            "services": services,
            "cta": cta,
        },
        "section_order_part1": section_order,
        "tokens": tokens,
        "business_type": business_type,
    }


# ───────────────────────────────────────────────────────────────────────────
# 7. RENDERERS — convert the spec into (a) a <style> block injected into
#    <head>, and (b) prompt blocks prepended to Pass 1 / Pass 2 prompts.
# ───────────────────────────────────────────────────────────────────────────

def _google_fonts_url(heading: str, body: str) -> str:
    def fam(name: str, weights: str) -> str:
        return f"family={name.replace(' ', '+')}:wght@{weights}"
    if heading.strip().lower() == body.strip().lower():
        return f"https://fonts.googleapis.com/css2?{fam(heading,'400;500;600;700;800')}&display=swap"
    return (
        "https://fonts.googleapis.com/css2?"
        f"{fam(heading, '400;500;600;700;800')}&"
        f"{fam(body, '400;500;600;700')}&display=swap"
    )


def render_css_tokens(spec: dict) -> str:
    """Emit <link>+<style> block that locks in palette, fonts, radii, shadow,
    section-y padding via CSS custom properties.

    Injected into <head> AFTER Claude's output is repaired. Because these are
    `--lvrg-*` vars, the prompt asks Claude to reference them via
    `var(--lvrg-accent)` etc.; even if Claude drifts and writes a hard-coded
    colour, the rest of the page stays themed."""
    p = spec["palette"]
    t = spec["tokens"]
    f = spec["fonts"]
    url = _google_fonts_url(f["heading"], f["body"])
    return f"""<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="{url}" rel="stylesheet">
<style id="lvrg-design-tokens">
  :root {{
    --lvrg-bg: {p['bg']};
    --lvrg-surface: {p['surface']};
    --lvrg-surface-alt: {p['surface_alt']};
    --lvrg-text: {p['text_primary']};
    --lvrg-text-secondary: {p['text_secondary']};
    --lvrg-text-muted: {p['text_muted']};
    --lvrg-border: {p['border']};
    --lvrg-accent: {p['accent']};
    --lvrg-accent-dark: {p['accent_dark']};
    --lvrg-accent-soft: {p['accent_soft']};
    --lvrg-accent-contrast: {p['accent_contrast']};
    --lvrg-radius-md: {t['radius_md']};
    --lvrg-radius-lg: {t['radius_lg']};
    --lvrg-shadow-md: {t['shadow_md']};
    --lvrg-shadow-lg: {t['shadow_lg']};
    --lvrg-section-y: {t['section_y']};
    --lvrg-heading-font: "{f['heading']}", system-ui, -apple-system, Segoe UI, sans-serif;
    --lvrg-body-font: "{f['body']}", system-ui, -apple-system, Segoe UI, sans-serif;
  }}
  /* ── Global layout safety (applies to every generated site) ───────────────
     Prevents the lower-section collapse failure modes:
     - box-sizing:border-box → padding never blows out a sized column
     - img/svg/video max-width:100% → no image overflows its container
     - body overflow-x:hidden → no horizontal scroll on any viewport
     - section/footer overflow-wrap → long unbroken strings (emails, URLs)
       wrap cleanly instead of stretching a column off-screen
     - section min-width:0 → flex/grid children can shrink below content size,
       allowing proper responsive stacking
  */
  *, *::before, *::after {{ box-sizing: border-box; }}
  html, body {{
    background: var(--lvrg-bg);
    color: var(--lvrg-text);
    font-family: var(--lvrg-body-font);
    font-weight: {t['type_weight_body']};
    margin: 0;
    overflow-x: hidden;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
  }}
  img, svg, video, picture, canvas {{
    max-width: 100%;
    height: auto;
    display: block;
  }}
  section, footer, header, main, article {{
    overflow-wrap: break-word;
    word-wrap: break-word;
    min-width: 0;
  }}
  h1, h2, h3, h4, h5, h6 {{
    font-family: var(--lvrg-heading-font);
    font-weight: {t['type_weight_heading']};
    color: var(--lvrg-text);
    letter-spacing: -0.01em;
    overflow-wrap: break-word;
  }}
  /* Long strings (emails, URLs, single-word "supercalifragilistic" names) never
     break the layout — they wrap inside their cell. */
  a, p, li, span, div {{ overflow-wrap: break-word; }}
  /* Buttons and pill-shaped CTAs should never stretch full-width by accident. */
  a[href]:not([class]) {{ word-break: normal; }}
  @media (max-width: 768px) {{
    :root {{ --lvrg-section-y: calc({t['section_y']} * 0.6); }}
  }}
</style>"""


def render_part1_design_block(spec: dict) -> str:
    """Prompt block prepended to Pass 1. Tells Claude the section order +
    layout variant for each section + which CSS vars to reference."""
    p = spec["palette"]
    t = spec["tokens"]
    f = spec["fonts"]
    pers = spec["personality_data"]
    order = " → ".join(spec["section_order_part1"])

    hero = spec["variants"]["hero"]
    nav = spec["variants"]["nav"]
    soc = spec["variants"]["social_proof"]
    svc = spec["variants"]["services"]

    # Note: we deliberately use lowercase section names in this prompt block so
    # we don't trip the "no-section-labels-as-visible-text" guard or any tests
    # that assert e.g. "TESTIMONIALS" not in part1 prompt.
    return f"""━━━ DESIGN VARIATION SPEC — follow exactly ━━━

Personality: {spec['personality']}
Mood: {pers['mood']}
Section density: {pers['section_density']}  •  Card style: {pers['card_style']}  •  Button style: {pers['button_style']}

Typography (Google Fonts ARE pre-loaded in <head>, just reference the font-family):
  Headings: "{f['heading']}"  →  use for every h1/h2/h3
  Body:     "{f['body']}"      →  use for paragraphs, labels, buttons

CSS custom properties are pre-declared on :root in <head>. USE THESE VARS in your
inline styles — they guarantee the palette stays consistent even if you drift:
  background           → var(--lvrg-bg)
  panel / card surface → var(--lvrg-surface)
  alternating section  → var(--lvrg-surface-alt)
  primary text         → var(--lvrg-text)
  secondary text       → var(--lvrg-text-secondary)
  muted text           → var(--lvrg-text-muted)
  borders / hairlines  → var(--lvrg-border)
  primary accent       → var(--lvrg-accent)             (use for CTAs, key callouts)
  accent darker        → var(--lvrg-accent-dark)        (hover state for accent)
  accent tint          → var(--lvrg-accent-soft)        (subtle bg highlight)
  contrast on accent   → var(--lvrg-accent-contrast)    (button text on accent bg)
  card radius          → var(--lvrg-radius-md)
  large card radius    → var(--lvrg-radius-lg)
  card shadow          → var(--lvrg-shadow-md)
  hero / large shadow  → var(--lvrg-shadow-lg)
  section vert padding → var(--lvrg-section-y)
Example usage: style="background:var(--lvrg-surface);border-radius:var(--lvrg-radius-md);box-shadow:var(--lvrg-shadow-md);padding:var(--lvrg-section-y) 24px;"

━━━ section order (after claim-bar + nav) ━━━
{order}

━━━ section variant instructions ━━━
nav variant ({nav['id']}): {nav['spec']}
hero variant ({hero['id']}): {hero['spec']}
social-proof variant ({soc['id']}): {soc['spec']}
services variant ({svc['id']}): {svc['spec']}

The variant choices above are NOT suggestions — pick the exact layout described.
Two different prospects must end up with visibly different pages because of these
variant assignments. Do not collapse them back to a generic 3-card grid hero+services.

━━━ end design variation spec ━━━
"""


def render_part2_design_block(spec: dict) -> str:
    """Prompt block prepended to Pass 2. Reinforces consistency with Part 1
    (same fonts, same CSS vars, same personality) and specifies the CTA variant."""
    pers = spec["personality_data"]
    cta = spec["variants"]["cta"]
    f = spec["fonts"]
    return f"""━━━ DESIGN VARIATION SPEC — must match Part 1 ━━━

Personality: {spec['personality']} — {pers['mood']}
Card style: {pers['card_style']}  •  Button style: {pers['button_style']}

Reuse the SAME CSS custom properties already declared in <head> on Part 1:
var(--lvrg-bg), var(--lvrg-surface), var(--lvrg-surface-alt), var(--lvrg-text),
var(--lvrg-text-secondary), var(--lvrg-border), var(--lvrg-accent),
var(--lvrg-accent-contrast), var(--lvrg-radius-md), var(--lvrg-radius-lg),
var(--lvrg-shadow-md), var(--lvrg-shadow-lg), var(--lvrg-section-y).

Reuse the SAME fonts already loaded:
  Headings: "{f['heading']}"
  Body:     "{f['body']}"

━━━ cta variant ({cta['id']}) ━━━
{cta['spec']}

The cta variant above is NOT a suggestion — render exactly this layout, not a
generic full-width band with a button (unless that IS the chosen variant).

━━━ end design variation spec ━━━
"""


def hero_image_strategy(spec: dict) -> str:
    """Return one of: 'background_overlay', 'inline_column', 'no_image'.
    Lets generator.py build the right hero_bg_instruction for the chosen variant."""
    return spec["variants"]["hero"]["image_usage"]
