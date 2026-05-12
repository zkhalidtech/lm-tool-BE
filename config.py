"""
LVRG Lead Magnet Engine — Config
Reads from environment variables only (rotate any keys that were previously committed).
"""

import os

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
INSTANTLY_API_KEY = os.environ.get("INSTANTLY_API_KEY", "").strip()

# Sender identity
SENDER_NAME = "Josh"
SENDER_EMAIL = "adam@mobiloptimismrade.com"
SENDER_AGENCY = "LVRG Agency"
SENDER_WEBSITE = "lvrg.com"
SENDER_PHONE = "619.361.7484"
BOOKING_URL = "https://theresandiego.com/advertise/"

# Vercel app URL — set APP_URL env var in Railway + local .env
# Preview links are served at {APP_URL}/p/{prospect_id}
APP_URL = os.environ.get("APP_URL", "https://your-app.vercel.app").rstrip("/")
PREVIEW_BASE_URL = f"{APP_URL}/p"

# Output dirs
ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
SITES_DIR = os.path.join(ENGINE_DIR, "output", "sites")
EMAILS_DIR = os.path.join(ENGINE_DIR, "output", "emails")
INTEL_DIR = os.path.join(ENGINE_DIR, "output", "intel")

os.makedirs(SITES_DIR, exist_ok=True)
os.makedirs(EMAILS_DIR, exist_ok=True)
os.makedirs(INTEL_DIR, exist_ok=True)
