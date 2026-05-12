"""
LVRG Lead Magnet Engine — Supabase Storage Deployer
Uploads generated site HTML to Supabase Storage (public bucket: sites).
Preview is served by the Next.js frontend at {APP_URL}/p/{prospect_id}.
"""

import os
from supabase_client import upload_to_storage
from config import PREVIEW_BASE_URL


def deploy_site(prospect_id: str, site_dir: str) -> str:
    """Upload site HTML to Supabase Storage. Returns the public preview URL."""

    print(f"  [deploy] Uploading {prospect_id} to Supabase Storage...")

    index_path = os.path.join(site_dir, "index.html")
    with open(index_path, "rb") as f:
        html_bytes = f.read()

    upload_to_storage(prospect_id, html_bytes)

    public_url = f"{PREVIEW_BASE_URL}/{prospect_id}"
    print(f"  [deploy] Live at: {public_url}")
    return public_url
