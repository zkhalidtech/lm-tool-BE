"""
LVRG Engine — FastAPI Server
Wraps the engine pipeline as an HTTP API with SSE streaming.
"""

# Load .env before any engine modules read os.environ at import time
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import asyncio
import json
import os
import sys
from datetime import datetime
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# Engine modules
from intel import scrape_site, grade_site
from generator import generate_site, generate_email
from deploy import deploy_site
from supabase_client import upsert_lead, log_event, update_engine_queue_result

app = FastAPI(title="LVRG Engine API", version="1.2.0")


def _supabase_rest_base() -> str:
    return (
        os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL") or ""
    ).rstrip("/")


@app.on_event("startup")
async def run_migrations():
    """Add any missing columns to engine_queue on startup."""
    import urllib.request, urllib.error, json as _json
    SUPABASE_URL = _supabase_rest_base()
    SERVICE_KEY = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not SUPABASE_URL or not SERVICE_KEY:
        print("[startup] SUPABASE_URL or SUPABASE_SERVICE_KEY not set, skipping column check")
        return
    # Try to SELECT the new columns — if they don't exist PostgREST returns a 400
    # We add them by calling a stored procedure if it exists, otherwise skip gracefully
    try:
        url = f"{SUPABASE_URL}/rest/v1/engine_queue?select=preview_url,email_json&limit=0"
        req = urllib.request.Request(url, headers={
            "apikey": SERVICE_KEY,
            "Authorization": f"Bearer {SERVICE_KEY}",
        })
        urllib.request.urlopen(req)
        print("[startup] engine_queue columns OK")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if "does not exist" in body:
            print("[startup] engine_queue missing columns — please run migration SQL:")
            print("  ALTER TABLE engine_queue")
            print("    ADD COLUMN IF NOT EXISTS preview_url text,")
            print("    ADD COLUMN IF NOT EXISTS email_json jsonb;")
        else:
            print(f"[startup] column check: {e.code} {body[:200]}")
    except Exception as e:
        print(f"[startup] migration check skipped: {e}")


# Allowed browser origins. Extend via ALLOWED_ORIGINS env var (comma-separated).
# joshclifford.github.io is required — deployed chat widgets call /chat from there.
_DEFAULT_ORIGINS = [
    "http://localhost:3000",
    "http://localhost:8766",
    "https://lm-tool-production.up.railway.app",
    "https://joshclifford.github.io",
    "null",  # file:// origin — needed when previewing generated sites locally
]
_extra = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
ALLOWED_ORIGINS = list(dict.fromkeys(_DEFAULT_ORIGINS + _extra))  # deduplicate, preserve order

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Engine-Secret"],
)


class BuildRequest(BaseModel):
    domain: str
    no_deploy: bool = False
    offer: str = "Smart Site"
    cta: str = "Book a Call"
    notes: str = ""


class ChatRequest(BaseModel):
    message: str
    business_name: str
    intel: dict  # full intel object baked into each widget
    history: list = []  # [{"role": "user"|"assistant", "content": "..."}]


def sse(type: str, **kwargs) -> str:
    return f"data: {json.dumps({'type': type, **kwargs})}\n\n"


async def run_pipeline(domain: str, no_deploy: bool, offer: str, cta: str, notes: str = "") -> AsyncGenerator[str, None]:
    """Run the full engine pipeline, yielding SSE events."""

    loop = asyncio.get_event_loop()

    def emit(type: str, **kwargs):
        # Returns the SSE string — we collect and yield from outside
        pass

    logs = []

    def log(text: str, level: str = "info"):
        line = sse("log", text=text, level=level)
        logs.append(line)

    try:
        # ── Step 0: Fetch queue contact data (Scout may have found email/phone) ──
        queue_contact = {}
        try:
            import urllib.request as _ur, urllib.parse as _up
            _base = _supabase_rest_base()
            _key = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY", "")
            if _base and _key:
                _url = f"{_base}/rest/v1/engine_queue?select=email,phone&domain=eq.{_up.quote(domain, safe='')}&limit=1"
                _req = _ur.Request(_url, headers={"apikey": _key, "Authorization": f"Bearer {_key}"})
                with _ur.urlopen(_req) as _res:
                    _rows = __import__('json').loads(_res.read().decode())
                    if _rows: queue_contact = _rows[0]
        except Exception:
            pass

        # ── Step 1: Intel ────────────────────────────────────────────
        yield sse("log", text=f"Reading {domain}...", level="info")
        intel = await loop.run_in_executor(None, scrape_site, domain)
        # Prefer Scout-found email/phone over scraped (Scout finds real contact emails)
        if queue_contact.get("email"): intel["email"] = queue_contact["email"]
        if queue_contact.get("phone"): intel["phone"] = queue_contact["phone"]
        yield sse("log", text=f"Got intel for {intel['business_name']}", level="success")
        yield sse("intel", data=intel)

        # ── Step 2: Grade ────────────────────────────────────────────
        yield sse("log", text="Grading site...", level="info")
        grade = await loop.run_in_executor(None, grade_site, intel)
        yield sse("log", text=f"Score: {grade['total']}/10 — {grade['verdict']}", level="success")
        yield sse("grade", data=grade)

        if not grade["worth_targeting"]:
            yield sse("log", text=f"Score {grade['total']}/10 — noted, building anyway", level="info")

        # ── Step 3: Generate site ────────────────────────────────────
        from slugify import slugify
        # Strip www. before building slug so www.foo.com → foo not www
        _slug_domain = domain.removeprefix('www.')
        prospect_id = slugify(_slug_domain.split(".")[0]) or slugify(_slug_domain.replace(".", "-"))

        yield sse("log", text="Generating Smart Site with Claude...", level="info")
        if notes:
            yield sse("log", text=f"Notes: {notes}", level="info")
        site_dir = await loop.run_in_executor(None, generate_site, intel, prospect_id, notes)
        yield sse("log", text="Site generated", level="success")

        # ── Step 4: Deploy ───────────────────────────────────────────
        # TEMP: GitHub Pages deploy disabled — no GITHUB_TOKEN available right now.
        # When the key is added back, restore the original block below.
        preview_url = None
        index_path = os.path.join(site_dir, "index.html")
        # Auto-open the generated site in the default browser for instant local preview.
        try:
            import webbrowser
            webbrowser.open(f"file://{os.path.abspath(index_path)}")
            yield sse("log", text=f"Opened in browser: {index_path}", level="success")
        except Exception as e:
            yield sse("log", text=f"Could not auto-open: {e}", level="dim")
        preview_url = f"file://{os.path.abspath(index_path)}"

        # --- Original deploy block (re-enable when GITHUB_TOKEN is available) ---
        # if not no_deploy:
        #     yield sse("log", text="Deploying to GitHub Pages...", level="info")
        #     try:
        #         preview_url = await loop.run_in_executor(None, deploy_site, prospect_id, site_dir)
        #         yield sse("log", text=f"Live at {preview_url}", level="success")
        #     except Exception as e:
        #         yield sse("log", text=f"Deploy failed: {e} — continuing", level="error")
        #         preview_url = None
        # else:
        #     preview_url = f"[local] {site_dir}/index.html"

        # ── Step 5: Generate email ───────────────────────────────────
        yield sse("log", text="Writing outreach messaging...", level="info")
        email_data = await loop.run_in_executor(None, generate_email, intel, grade, prospect_id)
        yield sse("log", text="Messaging ready", level="success")

        # ── Step 6: Save to Supabase (skip on no_deploy / smoke test runs) ──
        if no_deploy:
            yield sse("log", text="Skipping Supabase save (test mode)", level="dim")
        else:
            yield sse("log", text="Saving to Supabase...", level="info")
            try:
                sb_lead = await loop.run_in_executor(
                    None,
                    lambda: upsert_lead(
                        domain=domain,
                        intel=intel,
                        grade=grade,
                        preview_url=preview_url,
                        email_data=email_data,
                        instantly_lead_id=None,
                        instantly_campaign_id=None,
                        offer=offer,
                        cta=cta,
                        status="built",
                    )
                )
                if sb_lead:
                    await loop.run_in_executor(
                        None,
                        lambda: log_event(sb_lead["id"], "site_built", {
                            "preview_url": preview_url,
                            "score": grade.get("total"),
                        })
                    )
            except Exception as e:
                yield sse("log", text=f"Supabase save failed: {e}", level="error")

            # ── Step 6b: Write-back preview_url + email_json to engine_queue ──
            try:
                await loop.run_in_executor(
                    None,
                    lambda: update_engine_queue_result(domain, preview_url, email_data)
                )
            except Exception as e:
                yield sse("log", text=f"Queue write-back skipped: {e}", level="dim")

        # ── Done ─────────────────────────────────────────────────────
        yield sse("result", payload={
            "preview_url": preview_url,
            "email": email_data,
            "intel": intel,
            "grade": grade,
        })
        yield sse("done", status="complete")

    except Exception as e:
        yield sse("log", text=f"Pipeline error: {e}", level="error")
        yield sse("error", text=str(e))
        yield sse("done", status="error")


def _check_engine_secret(request: Request) -> bool:
    """Validates X-Engine-Secret header when ENGINE_SECRET env var is set."""
    secret = os.environ.get("ENGINE_SECRET", "").strip()
    if not secret:
        return True  # secret not configured — allow (local dev)
    return request.headers.get("X-Engine-Secret", "") == secret


@app.post("/build")
async def build(req: BuildRequest, request: Request):
    """Run the full engine pipeline for a domain. Returns SSE stream."""
    if not _check_engine_secret(request):
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    domain = req.domain.strip().lower()
    domain = domain.replace("https://", "").replace("http://", "")
    domain = domain.split("/")[0].split("?")[0].strip()
    if not domain:
        raise HTTPException(status_code=400, detail="domain is required")

    return StreamingResponse(
        run_pipeline(domain, req.no_deploy, req.offer, req.cta, req.notes),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/chat")
async def chat(req: ChatRequest, request: Request):
    """AI chat endpoint for Smart Site widgets. Responds as the business."""
    if not _check_engine_secret(request):
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    import anthropic
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    client = anthropic.Anthropic(api_key=key)

    # Build system prompt from scraped intel
    intel = req.intel
    business_name = intel.get('business_name', 'this business')
    phone = intel.get('phone', '')
    hours = intel.get('hours', '')
    location = intel.get('location', '')
    email = intel.get('email', '')
    services = intel.get('services') or []
    services_str = ', '.join(services) if services else 'not listed'
    content_notes = intel.get('content_notes', '') or ''
    cta_angle = intel.get('cta_angle', 'contact us')

    # All Google reviews (up to 5 — sorted highest-rated first by places.py)
    reviews = intel.get('reviews') or []
    real_reviews = [r for r in reviews if (r.get('text') or '').strip()][:5]
    rating = intel.get('google_rating', 0)
    total = intel.get('google_total_ratings', 0)

    if real_reviews:
        review_lines = "\n".join(
            f'  [{i+1}] {r.get("author", "Anonymous")} ({int(r.get("rating", 5))}★, {r.get("time_ago", "recently")}):\n      "{(r.get("text") or "").strip()}"'
            for i, r in enumerate(real_reviews)
        )
        rating_line = f"{rating}★ average across {total} Google reviews" if rating else "rating not available"
    else:
        review_lines = "  (no Google reviews available for this business)"
        rating_line = "rating not available"

    # Full raw site content — chatbot's primary knowledge source for descriptions, FAQs, specials
    raw_text = (intel.get('raw_text') or '').strip()

    # Debug log — confirms what intel the widget actually sent us
    first_review_preview = (real_reviews[0].get('text', '')[:80] + '...') if real_reviews else '(none)'
    raw_preview = raw_text[:120].replace('\n', ' ') + '...' if raw_text else '(empty)'
    print(f"  [chat] {business_name}")
    print(f"         phone:{phone or '-'}  hours:{bool(hours)}  loc:{location[:60] or '-'}")
    print(f"         reviews:{len(real_reviews)} (first: {first_review_preview})")
    print(f"         raw_text:{len(raw_text)}chars  content_notes:{len(content_notes)}chars")
    print(f"         raw preview: {raw_preview}")

    system = f"""You are the live AI assistant for {business_name}. You work here. You have full knowledge of the business below.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
WEBSITE CONTENT (PRIMARY KNOWLEDGE SOURCE — search this first for any question about
menu, food, drinks, products, services, history, philosophy, opening info, FAQs).
This is the actual text from {business_name}'s website. Read it carefully — the answer
to most questions is in here.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{raw_text if raw_text else '(no website content available)'}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

VERIFIED BUSINESS FACTS:
• Business name: {business_name}
• About: {intel.get('description', '')}
• Services / what we offer: {services_str}
• Address / Location: {location or 'not listed'}
• Phone: {phone or 'not listed'}
• Email: {email or 'not listed'}
• Hours: {hours or 'not listed'}
• Social proof: {intel.get('social_proof', '') or 'not listed'}

MENU / PRICES / NAMED ITEMS (use exact wording):
{content_notes if content_notes else '(none extracted — pull menu items, drinks, food from WEBSITE CONTENT above)'}

GOOGLE REVIEWS ({rating_line}, sorted highest-rated first):
{review_lines}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
ANSWER PROTOCOL — follow this exact order for every user question:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

STEP 1 — Identify question type:
  • Phone / contact number? → use VERIFIED FACTS
  • Hours / opening times? → use VERIFIED FACTS
  • Address / location / where? → use VERIFIED FACTS (state exactly what's listed; never say only "United Kingdom" if more is listed)
  • Reviews / ratings / "top review" / "what do customers say"? → use GOOGLE REVIEWS
  • Menu / food / drinks / breakfast / lunch / coffee / what do you serve / what's on offer / popular items / specials → SEARCH WEBSITE CONTENT line by line for product names, dish names, drink types, prices. List what you find.
  • About / history / philosophy / founders / story → SEARCH WEBSITE CONTENT carefully
  • Anything else → SEARCH WEBSITE CONTENT first, then VERIFIED FACTS

STEP 2 — Search the relevant section(s) above. Quote real names, real prices, real review text. Do NOT paraphrase prices or menu items.

STEP 3 — If the answer is genuinely not in any section, say ONE of:
  • "Best to call us at {phone or 'the number on our site'} — we'll have the exact details for you."
  • "Pop in and we'll sort it for you" (if location is known)
NEVER say "I'll have the team follow up" — there is no team callback system.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
HARD RULES — break these and the answer is wrong:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

1. NEVER use markdown formatting. NO **bold**, NO *italic*, NO _underline_, NO # headings, NO bullet points with - or *.
   Plain text only. The widget renders raw text — markdown shows as literal asterisks. To emphasise, use natural language.

2. If GOOGLE REVIEWS has any reviews listed → answer "top review / best review / what do customers say" by quoting
   the FIRST review in the list (highest-rated) with the author's name. Example:
   "Our top review is from Lewis Newman: 'Nice decor inside, with a relaxing atmosphere...' — he gave us 5 stars."
   NEVER say "I don't have access to reviews" if any review is in the list above.

3. If WEBSITE CONTENT contains menu items / drinks / food names → list them when asked. Even if no prices are listed,
   give the names. Example: "We do all-day breakfast — bacon sandwiches, sourdough toasties, full English plates,
   plus our signature smoothies." NEVER say "I don't have menu details" if WEBSITE CONTENT has any food/drink terms.

4. If location field has more than just a country → give the full string. Example: state "262 Brixton Hill, London"
   not just "United Kingdom". If location is genuinely just "United Kingdom", say so honestly and offer the phone.

5. NEVER say: "I don't have access to", "I cannot provide", "I'm an AI", "I'm not able to", "as a chatbot",
   "I'll have the team follow up", "I'll get back to you". You ARE the source — you do not delegate.

6. NEVER invent: prices, menu items, awards, certifications, staff names, founding year, capacity, addresses.
   If it's not in the data above, say so honestly and redirect to phone/visit.

7. NEVER recommend competitors or other businesses.

8. Tone: warm, confident, local, helpful. 2-3 sentences max. Use "we" and "us". End with a soft invite to
   {cta_angle} when natural — but no pushy CTAs every reply.

9. Never reveal you are an AI unless directly asked. If asked, say "I'm the AI assistant for {business_name}, here to help with anything about us."
"""

    messages = req.history[-10:] + [{"role": "user", "content": req.message}]

    # Sonnet 4.6 — Haiku was failing to use the GOOGLE REVIEWS / WEBSITE CONTENT sections
    # reliably. Sonnet follows the structured prompt and quotes the data verbatim.
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        system=system,
        messages=messages,
    )

    reply = response.content[0].text.strip()
    # Strip markdown that the widget can't render (bold, italic, headings, bullet markers)
    import re as _re
    reply = _re.sub(r'\*\*(.+?)\*\*', r'\1', reply)   # **bold** → bold
    reply = _re.sub(r'(?<!\w)\*([^*\n]+?)\*(?!\w)', r'\1', reply)  # *italic* → italic
    reply = _re.sub(r'(?<!\w)_([^_\n]+?)_(?!\w)', r'\1', reply)    # _italic_ → italic
    reply = _re.sub(r'^#{1,6}\s+', '', reply, flags=_re.MULTILINE) # # headings
    reply = _re.sub(r'^[\-\*]\s+', '', reply, flags=_re.MULTILINE) # bullet markers

    return {"reply": reply}


@app.post("/migrate")
async def migrate():
    """Admin endpoint: add missing columns to engine_queue.
    Requires SUPABASE_SERVICE_KEY env var with DDL privileges.
    Since PostgREST can't run DDL, this returns the SQL to run manually."""
    sql = (
        "ALTER TABLE engine_queue "
        "ADD COLUMN IF NOT EXISTS preview_url text, "
        "ADD COLUMN IF NOT EXISTS email_json jsonb;"
    )
    # Check if columns already exist
    import urllib.request, urllib.error
    SUPABASE_URL = _supabase_rest_base()
    KEY = os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not SUPABASE_URL or not KEY:
        return {"status": "error", "detail": "Set SUPABASE_URL and SUPABASE_SERVICE_KEY"}
    try:
        url = f"{SUPABASE_URL}/rest/v1/engine_queue?select=preview_url,email_json&limit=0"
        req = urllib.request.Request(url, headers={"apikey": KEY, "Authorization": f"Bearer {KEY}"})
        urllib.request.urlopen(req)
        return {"status": "columns_exist", "message": "preview_url and email_json already present"}
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        if "does not exist" in body:
            return {"status": "migration_needed", "sql": sql,
                    "instructions": "Run this SQL in Supabase dashboard > SQL Editor"}
        return {"status": "error", "detail": body[:300]}


@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.2.0"}


@app.get("/")
async def root():
    return {"service": "LVRG Engine API", "endpoints": ["/build", "/health"]}
