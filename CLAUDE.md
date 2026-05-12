# LVRG Engine — lvrg-engine

Python FastAPI backend that runs the AI lead magnet pipeline: scrape prospect site → extract intel → grade → generate preview HTML site → deploy to GitHub Pages → generate cold email → save to Supabase. Communicates with the Next.js frontend via SSE streaming.

## Dev Setup

```bash
# Python 3.13+ required
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Copy and fill env
cp .env.example .env

# Run the API server (port 8766 — matches ENGINE_URL in lm-tool dev)
uvicorn api:app --host 0.0.0.0 --port 8766 --reload

# Or run CLI batch processor
python run_engine.py thelittledoor.com
python run_engine.py --file domains.txt --no-deploy
```

## Module Map

| File | Responsibility |
|------|---------------|
| `api.py` | FastAPI app. Endpoints: `POST /build` (SSE pipeline), `POST /chat` (widget AI), `POST /migrate` (admin), `GET /health`. |
| `intel.py` | `scrape_site(domain)` → Firecrawl (JS-rendered) with requests fallback → Claude Haiku extract 22 fields → Google Places enrichment (real reviews + verified contact) → `grade_site(intel)` → 0–10 score. |
| `places.py` | `fetch_place_data(business_name, location)` → Google Places API: Find Place → Place Details → returns reviews, rating, verified address/phone/hours. Returns `{}` when key absent. |
| `generator.py` | `generate_site(intel, prospect_id, notes)` → 2-pass Claude Opus (2×6K tokens, Pass 2 prefilled) + chat widget injection. `generate_email(intel, grade, prospect_id)` → Claude Opus 3 subject variants + body JSON. |
| `deploy.py` | `deploy_site(prospect_id, site_dir)` → GitHub Git Data API: blob → tree → commit → ref update. No `git clone`. |
| `instantly.py` | `get_or_create_campaign(name)`, `add_lead(campaign_id, intel, email_data)` against Instantly v2 API. |
| `supabase_client.py` | `upsert_lead(...)`, `log_event(...)`, `update_engine_queue_result(...)`, `update_lead_status(...)` via urllib PostgREST. |
| `config.py` | Loads all env vars + hardcoded sender identity. See "Hardcoded Values" below. |
| `run_engine.py` | CLI batch runner. Same pipeline as `/build` endpoint but also handles Instantly push. |

## Full Pipeline (`POST /build`)

```
1. Normalize domain (strip protocol/path/query)
2. Fetch contact email/phone from engine_queue (Scout pre-populated)
3. scrape_site(domain)
   ├─ Firecrawl (JS-rendered markdown + images) → fallback to requests if key missing
   └─ Claude Haiku → extract 21 fields as JSON
4. grade_site(intel) → 7-dimension score (0–10), worth_targeting if 2≤score≤7
5. generate_site(intel, prospect_id, notes)
   ├─ Pass 1: Claude Opus (6K tokens) → above-fold HTML (head + claim bar + nav + hero + social proof + services)
   ├─ Pass 2: Claude Opus (6K tokens, prefilled with Pass 1) → below-fold (testimonials + CTA + footer)
   └─ inject chat widget → save to output/sites/{prospect_id}/index.html
6. deploy_site(prospect_id, site_dir)           # skipped if no_deploy=True
   └─ GitHub Git Data API → joshclifford/lvrg-previews
7. generate_email(intel, grade, prospect_id)
   └─ Claude Opus (1.5K tokens) → subject_a/b/c + body JSON
8. upsert_lead(...) → Supabase leads table
   log_event("site_built", {preview_url, score})
9. update_engine_queue_result(domain, preview_url, email_json) → engine_queue table
10. Yield SSE: result {preview_url, email, intel, grade}, then done
```

SSE event types: `log`, `intel`, `grade`, `result`, `error`, `done`.

## Claude Models

| Function | Model | Tokens | Notes |
|----------|-------|--------|-------|
| `extract_intel_with_claude` | `claude-haiku-4-5` | 1,500 | Structured JSON extraction from scraped HTML |
| `generate_site` | `claude-opus-4-5` | 6,000 ×2 | 2-pass generation — Pass 2 prefilled with Pass 1 for consistency |
| `generate_email` | `claude-opus-4-5` | 1,500 | 3 subject variants + personalized body |
| `POST /chat` | `claude-haiku-4-5` | 300 | Real-time chat widget replies |

No prompt caching is implemented. Every call sends the full prompt.

## Grading Logic (`grade_site`)

7 dimensions averaged to produce a 0–10 score:

| Dimension | Scoring |
|-----------|---------|
| `value_prop` | tagline=7, description>30chars=5, else=2 |
| `primary_cta` | booking keyword=8, any cta=4, else=1 |
| `contact` | phone(4)+email(3)+location(3), max 10 |
| `social_proof` | >50chars=8, >10chars=5, else=2 |
| `hours` | present=6, else=2 |
| `chat` | "chat" not in missing=8, else=0 |
| `gaps` | 10−(2×count of [chat,booking,menu,email,phone,contact] in missing), floor 0 |

`worth_targeting = True` if `2 ≤ score ≤ 7` (sweet spot — weak enough to need us, strong enough to be real).

## Environment Variables

```bash
# Required
ANTHROPIC_API_KEY=          # Claude API
SUPABASE_URL=               # or NEXT_PUBLIC_SUPABASE_URL
SUPABASE_SERVICE_KEY=       # or SUPABASE_KEY or SUPABASE_ANON_KEY

# Required for deploy
GITHUB_TOKEN=               # Personal access token for joshclifford/lvrg-previews

# Optional
CHAT_ENDPOINT=              # Chat widget URL baked into generated HTML (default: prod Railway URL)
INSTANTLY_API_KEY=          # Only needed for CLI outreach
FIRECRAWL_API_KEY=          # Firecrawl API for JS-rendered content scraping
GOOGLE_PLACES_API_KEY=      # Google Places API — real customer reviews + verified contact info
LVRG_BRAND_ID=              # Override default brand UUID
```

Key priority for Supabase key: `SUPABASE_KEY` → `SUPABASE_SERVICE_KEY` → `SUPABASE_ANON_KEY`. Use the service key to bypass RLS.

## Hardcoded Values to Know

These live in `config.py` and `generator.py` — know them before touching outreach or deploy flows:

```python
# config.py
SENDER_NAME    = "Josh"
SENDER_EMAIL   = "adam@mobiloptimismrade.com"   # NOTE: name says Josh, email is adam@
SENDER_AGENCY  = "LVRG Agency"
SENDER_WEBSITE = "lvrg.com"
SENDER_PHONE   = "619.361.7484"

GITHUB_USER    = "joshclifford"          # personal account, not an org
GITHUB_REPO    = "lvrg-previews"
PREVIEW_BASE_URL = "https://joshclifford.github.io/lvrg-previews"

```

## Output Files

The engine writes to `output/` (ephemeral on Railway — lost on restart):

```
output/
  sites/{prospect_id}/index.html   # generated HTML sites
  emails/{prospect_id}.json        # generated email JSON
  intel/{domain}.json              # extracted intel cache
```

Supabase is the persistent record — output files are disposable.

## Deployment

```toml
# railway.toml
startCommand = "uvicorn api:app --host 0.0.0.0 --port $PORT"
healthcheckPath = "/health"
restartPolicyType = "on_failure"
```

Prod URL: `https://lvrg-engine-production.up.railway.app`

```bash
# Smoke test against prod
./smoke_test.sh

# Smoke test against local
./smoke_test.sh http://localhost:8766
```

## CLI Usage (`run_engine.py`)

```bash
python run_engine.py thelittledoor.com colicchio.com
python run_engine.py --file domains.txt
python run_engine.py thelittledoor.com --no-deploy --no-instantly
python run_engine.py thelittledoor.com --icp restaurant --city "San Diego, CA"
python run_engine.py thelittledoor.com --offer "Smart Site" --cta "Book a Call"
python run_engine.py thelittledoor.com --campaign "LVRG LM2 - Website Rebuild"
```

CLI adds a 2s delay between prospects to avoid API throttling. Processes serially.

## Known Issues & Gotchas

- **CORS** — allowlist configured in `api.py` (localhost:3000, lm-tool-production, joshclifford.github.io). Add new origins via `ALLOWED_ORIGINS` env var.
- **Engine secret** — set `ENGINE_SECRET` env var (same value in both engine and lm-tool) to lock `/build` and `/chat` endpoints.
- **Chat widget endpoint** — `_lvrgEndpoint` in the injected JS reads `CHAT_ENDPOINT` env var at generation time (defaults to prod Railway URL). Set `CHAT_ENDPOINT=http://localhost:8766/chat` in `.env` for local dev.
- **Sender name/email mismatch** — `SENDER_NAME="Josh"` but `SENDER_EMAIL` is adam@... Check `config.py` before any outreach changes.
- **`run_engine.py` log path** — writes to `output/run_<timestamp>.json` relative to the script location (fixed from old hardcoded Railway path).
- **`supabase_client.py` uses urllib not the supabase Python client** — the `supabase==2.15.2` package is installed but not used for DB calls. All DB access goes through raw urllib PostgREST requests.
- **No retry logic anywhere** — Claude, GitHub, Supabase, and Instantly calls all fail on first error. Wrap in retry if building reliability features.
- **Silent JSON parse failures** — if Claude returns malformed JSON in intel extraction, `extract_intel_with_claude` silently returns `{}` and the pipeline continues with empty fields. No logging of the raw response.
- **Output files are ephemeral** — Railway restarts wipe `output/`. Never treat local output as the source of truth; Supabase is.
- **`run_engine.py` duplicates pipeline logic** from `api.py` — both implement the same scrape → grade → generate → deploy → email flow. If you change the pipeline in one, check the other.
