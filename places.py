"""
LVRG Lead Magnet Engine — Google Places API integration.

Fetches real customer reviews + place details for a business so the chat widget
can reference real testimonials and the generated site's testimonial section can
use verbatim Google reviews instead of Claude-invented ones.

Two API calls per business:
  1. Find Place From Text  — resolve business name + location → place_id
  2. Place Details         — fetch reviews, rating, address, phone, website

Returns {} when GOOGLE_PLACES_API_KEY is missing or the business can't be matched,
so callers can safely fall back to scraped data.
"""

import json
import os
import unicodedata
import urllib.request
import urllib.parse


def fetch_place_data(business_name: str, location: str = "", domain: str = "") -> dict:
    """Fetch Google Place details + top reviews for a business.

    Returns dict with keys: place_id, name, rating, total_ratings, address,
    phone, website, reviews (list of {author, rating, text, time_ago}).
    Returns {} if the API key is missing or no matching place is found.
    """
    api_key = os.environ.get("GOOGLE_PLACES_API_KEY", "").strip()
    if not api_key or not business_name:
        return {}

    place_id = _find_place_id(business_name, location, api_key, domain=domain)
    if not place_id:
        return {}

    return _fetch_place_details(place_id, api_key)


def _ascii(text: str) -> str:
    """Strip accents and non-ASCII chars: 'Café' → 'Cafe'."""
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").strip()


def _places_query(query: str, api_key: str) -> str:
    """Single Find Place call. Returns place_id string or ''."""
    url = (
        "https://maps.googleapis.com/maps/api/place/findplacefromtext/json"
        f"?input={urllib.parse.quote(query)}"
        "&inputtype=textquery"
        "&fields=place_id"
        f"&key={api_key}"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
        status = data.get("status", "?")
        candidates = data.get("candidates", [])
        if candidates:
            return candidates[0].get("place_id", "")
        err = data.get("error_message", "")
        print(f"  [places] '{query}' → status={status}{' err=' + err if err else ''}")
        return ""
    except Exception as e:
        print(f"  [places] find_place failed: {e}")
        return ""


def _country_hint(domain: str) -> str:
    """Infer country from TLD for non-.com domains, e.g. .co.uk → 'UK'."""
    tld_map = {
        ".co.uk": "UK", ".uk": "UK", ".co.nz": "New Zealand", ".com.au": "Australia",
        ".ca": "Canada", ".ie": "Ireland", ".de": "Germany", ".fr": "France",
        ".es": "Spain", ".it": "Italy", ".nl": "Netherlands", ".sg": "Singapore",
    }
    domain_lower = domain.lower()
    for tld, country in tld_map.items():
        if domain_lower.endswith(tld):
            return country
    return ""


def _find_place_id(business_name: str, location: str, api_key: str, domain: str = "") -> str:
    """Resolve a business name + location to a Google place_id.

    Tries multiple queries to handle accented names, short locations, and non-US domains:
      1. name + location  (as-is)
      2. ASCII name + location  (Café → Cafe)
      3. ASCII name + location + country hint  (.co.uk → adds "UK")
      4. ASCII name only  (last resort)
    Returns "" if all attempts fail.
    """
    ascii_name = _ascii(business_name)
    country = _country_hint(domain)
    loc = location.strip()
    loc_with_country = f"{loc} {country}".strip() if country and country not in loc else loc

    attempts: list[str] = []
    # 1. Original full query
    attempts.append(f"{business_name} {loc}".strip())
    # 2. ASCII name + location
    if ascii_name != business_name:
        attempts.append(f"{ascii_name} {loc}".strip())
    # 3. With country hint appended (if not already there)
    if loc_with_country != loc:
        attempts.append(f"{ascii_name} {loc_with_country}".strip())
    # 4. Name only (no location)
    attempts.append(ascii_name)

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for q in attempts:
        if q and q not in seen:
            seen.add(q)
            unique.append(q)

    for query in unique:
        place_id = _places_query(query, api_key)
        if place_id:
            if query != unique[0]:
                print(f"  [places] matched via fallback query: '{query}'")
            return place_id

    print(f"  [places] no place found after {len(unique)} attempts")
    return ""


def _fetch_place_details(place_id: str, api_key: str) -> dict:
    """Fetch full place details (name, rating, reviews, contact)."""
    url = (
        "https://maps.googleapis.com/maps/api/place/details/json"
        f"?place_id={place_id}"
        "&fields=name,rating,user_ratings_total,reviews,formatted_address,formatted_phone_number,website,opening_hours"
        f"&key={api_key}"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        print(f"  [places] details failed: {e}")
        return {}

    result = data.get("result", {})
    raw_reviews = result.get("reviews", []) or []

    reviews = sorted(
        [
            {
                "author": r.get("author_name", ""),
                "rating": r.get("rating", 0),
                "text": (r.get("text", "") or "").strip(),
                "time_ago": r.get("relative_time_description", ""),
            }
            for r in raw_reviews
            if (r.get("text") or "").strip()  # skip rating-only reviews with no text
        ],
        key=lambda r: r["rating"],
        reverse=True,  # 5★ first
    )[:5]

    opening_hours = result.get("opening_hours", {}) or {}
    weekday_text = opening_hours.get("weekday_text", []) or []

    return {
        "place_id": place_id,
        "name": result.get("name", ""),
        "rating": result.get("rating", 0),
        "total_ratings": result.get("user_ratings_total", 0),
        "address": result.get("formatted_address", ""),
        "phone": result.get("formatted_phone_number", ""),
        "website": result.get("website", ""),
        "hours": "\n".join(weekday_text) if weekday_text else "",
        "reviews": reviews,
    }
