"""
LVRG Lead Magnet Engine — Prospect Intel Gatherer
Fetches site content via requests + Claude extraction.
Falls back gracefully if site is unreachable.
"""

import requests
import json
import os
import anthropic
from config import INTEL_DIR
from places import fetch_place_data

def _get_client():
    key = os.environ.get("ANTHROPIC_API_KEY") or ""
    return anthropic.Anthropic(api_key=key)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


def extract_images(html: str, base_url: str) -> list:
    """Extract usable image URLs from raw HTML. Priority: og:image > twitter:image > img tags."""
    import re
    from urllib.parse import urljoin

    found = []

    # og:image — site owner's intentional share/hero image
    og = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if not og:
        og = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html, re.IGNORECASE)
    if og:
        found.append(og.group(1).strip())

    # twitter:image
    tw = re.search(r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if not tw:
        tw = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']', html, re.IGNORECASE)
    if tw:
        found.append(tw.group(1).strip())

    # img tags — skip tiny icons, SVGs, tracking pixels
    for src in re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE):
        src = src.strip()
        if src.startswith('data:'):
            continue
        lower = src.lower()
        if any(x in lower for x in ['.svg', '.ico', 'favicon', 'pixel', 'beacon', 'track', '1x1', 'blank']):
            continue
        found.append(src)
        if len(found) >= 6:
            break

    # Make absolute, deduplicate, keep top 5
    result, seen = [], set()
    for url in found:
        abs_url = urljoin(base_url, url)
        if not abs_url.startswith(('http://', 'https://')):
            continue
        if abs_url not in seen:
            seen.add(abs_url)
            result.append(abs_url)
        if len(result) >= 5:
            break
    return result


def fetch_with_firecrawl(domain: str) -> tuple:
    """Fetch site via Firecrawl (JS-rendered, clean markdown). Returns (text, images).
    Returns ("", []) when key is absent or the call fails — caller falls back to requests."""
    api_key = os.environ.get("FIRECRAWL_API_KEY", "").strip()
    if not api_key:
        return "", []

    import urllib.request as _ur
    import urllib.error as _ue

    url = f"https://{domain}" if not domain.startswith("http") else domain
    payload = json.dumps({
        "url": url,
        "formats": ["markdown", "html"],
        "onlyMainContent": False,
    }).encode()

    req = _ur.Request(
        "https://api.firecrawl.dev/v1/scrape",
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with _ur.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())

        if not data.get("success"):
            print(f"  [intel] Firecrawl returned success=false for {domain}")
            return "", []

        result = data.get("data", {})
        markdown = result.get("markdown", "") or ""
        html = result.get("html", "") or ""
        metadata = result.get("metadata", {})

        # Images: og:image from Firecrawl metadata first, then regex-extract from html
        images = []
        og = metadata.get("ogImage", "")
        if og and og.startswith("http"):
            images.append(og)
        if html:
            for img in extract_images(html, url):
                if img not in images:
                    images.append(img)
        images = images[:5]

        text = markdown[:8000] if markdown else ""
        print(f"  [intel] Firecrawl OK — {len(text)} chars, {len(images)} images")
        return text, images

    except Exception as e:
        print(f"  [intel] Firecrawl failed: {e}")
        return "", []


def fetch_subpages(domain: str) -> str:
    """Try common subpages (/menu, /about, /contact, /food) and return combined text.

    Many sites keep their menu / pricing / about info on dedicated pages, not the homepage.
    We probe a handful of common paths and concatenate any content found, so the chatbot
    has access to menu items, prices, and about-us text.
    """
    base = f"https://{domain}" if not domain.startswith("http") else domain
    paths = ["/menu", "/menus", "/food", "/drinks", "/about", "/about-us", "/contact", "/services"]
    found_blocks: list[str] = []
    import re

    for path in paths:
        url = base.rstrip("/") + path
        try:
            resp = requests.get(url, headers=HEADERS, timeout=8, allow_redirects=True)
            if resp.status_code != 200:
                continue
            html = resp.text
            # Skip if we got redirected back to homepage (404 → home pattern)
            if len(html) < 500:
                continue
            # Strip scripts/styles, then tags
            text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
            if len(text) < 200:
                continue
            # Cap each subpage's contribution
            found_blocks.append(f"\n\n--- {path} ---\n{text[:3500]}")
            print(f"  [intel] Found subpage: {path} ({len(text)} chars)")
        except Exception:
            continue

    return "".join(found_blocks)


def fetch_site_content(domain: str) -> tuple:
    """Fetch raw HTML/text from a site. Returns (stripped_text, image_urls)."""
    url = f"https://{domain}" if not domain.startswith("http") else domain
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        import re
        raw_html = resp.text

        images = extract_images(raw_html, url)

        text = raw_html
        text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:4000], images
    except Exception as e:
        print(f"  [intel] Fetch failed: {e}")
        return "", []


def extract_intel_with_claude(domain: str, raw_text: str) -> dict:
    """Use Claude to extract structured intel from raw site content."""
    
    prompt = f"""Analyze this website content from {domain} and extract structured information.

WEBSITE CONTENT:
{raw_text}

Extract and return a JSON object with these fields:
- business_name: The name of the business (string)
- tagline: Their tagline or hero headline (string, empty if none)
- description: What the business does in 2-3 sentences (string)
- services: List of main services/offerings (array of strings)
- location: City, neighborhood, or address (string)
- phone: Phone number if present (string, empty if none)
- email: Email address if present (string, empty if none)
- hours: Business hours if present (string, empty if none)
- social_proof: Awards, years in business, testimonials, notable claims (string)
- key_cta: Their main call to action if any (string, empty if none)
- missing: Important elements missing from the site - be specific (string, e.g. "no chat widget, no online booking, no menu listed")
- brand_vibe: Describe the brand feel in 5-10 words (string, e.g. "dark moody speakeasy with gold accents")
- primary_color: Best guess at primary brand color as hex (string, e.g. "#1a1a2e")
- secondary_color: Secondary brand color as hex (string)
- business_type: One of: restaurant, bar, catering, coffee_shop, retail, craft_beverage, service, other (string)
- pain_point: The single biggest conversion problem with their current site in one sentence (string)
- chat_persona: How an AI chat agent should behave for this business in one sentence (string)
- cta_angle: The best CTA angle for this business - what they most want customers to do (string, e.g. "Book a Private Event", "Get a Free Quote", "Reserve a Table")
- owner_name: Owner or decision maker first name if mentioned anywhere on the site (string, empty if not found)
- neighborhood: Specific San Diego neighborhood or area, parsed from the location field (string, e.g. "North Park", "Little Italy", "Gaslamp", empty if unknown)
- content_notes: Specific real details found in the scraped content that should appear verbatim in the site copy — menu items with prices, named dishes or services, specific pricing tiers, awards with years, notable staff names, signature offerings, or any concrete detail that makes this business unique. Pull exact text from the content. (string, empty if nothing specific found)

Return ONLY valid JSON, no markdown, no explanation."""

    client = _get_client()
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    
    raw = response.content[0].text.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.split("```")[0]
    
    try:
        return json.loads(raw)
    except:
        return {}


def scrape_site(domain: str) -> dict:
    """Full intel gather for a prospect domain."""
    
    # Strip protocol, path, query — keep only the hostname
    domain = domain.strip().lower()
    domain = domain.replace("https://", "").replace("http://", "")
    domain = domain.split("/")[0].split("?")[0].strip()
    url = f"https://{domain}"
    print(f"  [intel] Fetching {url}...")

    # Firecrawl gives JS-rendered markdown + better images; fall back to plain requests
    raw_text, images = fetch_with_firecrawl(domain)
    if not raw_text:
        print(f"  [intel] Falling back to requests scraper...")
        raw_text, images = fetch_site_content(domain)

    # Probe common subpages (/menu, /about, /contact, etc.) and append any content.
    # Many sites keep their menu and pricing on dedicated pages, not the homepage.
    print(f"  [intel] Probing subpages for menu / about / contact content...")
    subpage_text = fetch_subpages(domain)
    if subpage_text:
        raw_text = raw_text + subpage_text

    if raw_text:
        print(f"  [intel] Extracting structured intel with Claude... ({len(raw_text)} chars total)")
        extracted = extract_intel_with_claude(domain, raw_text)
    else:
        extracted = {}

    business_name = extracted.get("business_name") or domain.split(".")[0].replace("-", " ").title()
    location = extracted.get("location", "")

    # Google Places enrichment — real reviews, verified phone/address/hours.
    # Returns {} if GOOGLE_PLACES_API_KEY is not set, so we fall back to scraped data.
    print(f"  [intel] Fetching Google Places data...")
    places_data = fetch_place_data(business_name, location, domain=domain)
    if places_data:
        print(f"  [intel] Places: {places_data.get('rating', '?')}★ "
              f"({places_data.get('total_ratings', 0)} ratings), "
              f"{len(places_data.get('reviews', []))} reviews")

    # Build final intel object — prefer Google Places data for contact info when available
    # (Places data is verified by Google, often more accurate than the website itself).
    # Address: if Places returns a full address (with comma — street + city) and the scraped
    # location is short/generic (e.g. just "United Kingdom"), prefer the Places address.
    places_address = places_data.get("address", "")
    if places_address and "," in places_address and (not location or len(location) < 20 or "," not in location):
        final_location = places_address
    else:
        final_location = location or places_address

    intel = {
        "domain": domain,
        "url": url,
        "business_name": business_name,
        "tagline": extracted.get("tagline", ""),
        "description": extracted.get("description", f"Local business at {domain}"),
        "services": extracted.get("services", []),
        "location": final_location,
        "phone": extracted.get("phone", "") or places_data.get("phone", ""),
        "email": extracted.get("email", ""),
        "hours": extracted.get("hours", "") or places_data.get("hours", ""),
        "social_proof": extracted.get("social_proof", ""),
        "key_cta": extracted.get("key_cta", ""),
        "missing": extracted.get("missing", "chat widget, clear CTA, contact info"),
        "brand_vibe": extracted.get("brand_vibe", "clean, modern local business"),
        "primary_color": extracted.get("primary_color", "#1a1a2e"),
        "secondary_color": extracted.get("secondary_color", "#c9a961"),
        "business_type": extracted.get("business_type", "other"),
        "pain_point": extracted.get("pain_point", "Visitors can't easily take action on the site"),
        "chat_persona": extracted.get("chat_persona", "Friendly assistant that answers questions and helps customers"),
        "cta_angle": extracted.get("cta_angle", "Get in Touch"),
        "owner_name": extracted.get("owner_name", ""),
        "neighborhood": extracted.get("neighborhood", ""),
        "content_notes": extracted.get("content_notes", ""),
        # Google Places enrichment fields
        "reviews": places_data.get("reviews", []),
        "google_rating": places_data.get("rating", 0),
        "google_total_ratings": places_data.get("total_ratings", 0),
        "google_place_id": places_data.get("place_id", ""),
        "images": images,
        "raw_text": raw_text[:15000],
    }

    print(f"  [intel] ✓ {intel['business_name']} — {intel['business_type']} — {intel['location']}")
    
    # Save
    os.makedirs(INTEL_DIR, exist_ok=True)
    slug = domain.replace(".", "_")
    with open(os.path.join(INTEL_DIR, f"{slug}.json"), "w") as f:
        json.dump(intel, f, indent=2)
    
    return intel


def grade_site(intel: dict) -> dict:
    """Score the site 0-10 against the LVRG rubric. Target: 2-7."""
    
    scores = {}
    
    scores["value_prop"] = 7 if intel.get("tagline") else (5 if len(intel.get("description","")) > 30 else 2)
    
    cta = (intel.get("key_cta") or "").lower()
    scores["primary_cta"] = 8 if any(w in cta for w in ["book","order","call","get","contact","buy","reserve","quote"]) else (4 if cta else 1)
    
    contact_score = 0
    if intel.get("phone"): contact_score += 4
    if intel.get("email"): contact_score += 3
    if intel.get("location"): contact_score += 3
    scores["contact"] = min(contact_score, 10)
    
    sp = intel.get("social_proof", "")
    scores["social_proof"] = 8 if len(sp) > 50 else (5 if len(sp) > 10 else 2)
    
    scores["hours"] = 6 if intel.get("hours") else 2
    
    missing = (intel.get("missing") or "").lower()
    has_chat = "chat" not in missing
    scores["chat"] = 8 if has_chat else 0
    
    gap_count = sum(1 for w in ["chat","booking","menu","email","phone","contact"] if w in missing)
    scores["gaps"] = max(0, 10 - gap_count * 2)
    
    total = round(sum(scores.values()) / len(scores))
    
    return {
        "scores": scores,
        "total": total,
        "verdict": get_verdict(total),
        "worth_targeting": 2 <= total <= 7
    }


def get_verdict(score: int) -> str:
    if score <= 2: return "Barely functional — may not convert well"
    if score <= 4: return "Weak — strong opportunity"
    if score <= 6: return "Mid — clear conversion gaps"
    if score <= 8: return "Good — may not need us"
    return "Strong — not a target"
