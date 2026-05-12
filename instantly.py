"""
LVRG Lead Magnet Engine — Instantly.ai Integration
Pushes prospects and email sequences to Instantly campaigns.
"""

import requests
import base64
import json
from config import INSTANTLY_API_KEY, SENDER_EMAIL, BOOKING_URL

# Key works as raw base64 string (not decoded)
API_KEY = INSTANTLY_API_KEY

BASE_URL = "https://api.instantly.ai/api/v2"

HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}


def get_or_create_campaign(name: str) -> str:
    """Get existing campaign ID or create a new one."""
    
    # List campaigns
    resp = requests.get(f"{BASE_URL}/campaigns", headers=HEADERS, params={"limit": 50})
    
    if resp.status_code == 200:
        campaigns = resp.json().get("items", [])
        for c in campaigns:
            if c.get("name") == name:
                print(f"  [instantly] Found existing campaign: {c['id']}")
                return c["id"]
    
    # Create new with required schedule
    payload = {
        "name": name,
        "email_list": [SENDER_EMAIL],
        "campaign_schedule": {
            "schedules": [{
                "name": "Default",
                "timing": {"from": "08:00", "to": "17:00"},
                "days": {"0": False, "1": True, "2": True, "3": True, "4": True, "5": True, "6": False},
                "timezone": "America/Los_Angeles"
            }]
        },
        "sequences": [{
            "steps": [{
                "type": "email",
                "delay": 0,
                "delay_unit": "days",
                "variants": [{
                    "subject": "{{subject}}",
                    "body": "{{body}}"
                }]
            }]
        }]
    }
    resp = requests.post(f"{BASE_URL}/campaigns", headers=HEADERS, json=payload)
    
    if resp.status_code in [200, 201]:
        campaign_id = resp.json().get("id")
        print(f"  [instantly] Created campaign: {campaign_id}")
        return campaign_id
    else:
        print(f"  [instantly] Campaign create error: {resp.status_code} {resp.text[:200]}")
        return None


def add_lead(campaign_id: str, intel: dict, email_data: dict) -> bool:
    """Add a lead to an Instantly campaign with personalized email."""
    
    business_name = intel.get("business_name", "there")
    contact_email = intel.get("email", "")
    
    if not contact_email:
        print(f"  [instantly] No email found for {business_name} — skipping Instantly push")
        return False
    
    recommended = email_data.get("recommended_subject", "b")
    subject_key = f"subject_{recommended}"
    subject = email_data.get(subject_key, email_data.get("subject_b", "Quick note"))
    body = email_data.get("body", "")
    
    payload = {
        "campaign_id": campaign_id,
        "email": contact_email,
        "first_name": business_name,
        "company_name": business_name,
        "website": intel.get("domain", ""),
        "personalization": body[:500],
    }
    
    resp = requests.post(f"{BASE_URL}/leads", headers=HEADERS, json=payload)
    
    if resp.status_code in [200, 201]:
        print(f"  [instantly] Lead added: {contact_email}")
        return True
    else:
        print(f"  [instantly] Lead add error: {resp.status_code} {resp.text[:300]}")
        return False


def get_campaigns() -> list:
    """List all campaigns — useful for debugging."""
    resp = requests.get(f"{BASE_URL}/campaigns", headers=HEADERS, params={"limit": 20})
    if resp.status_code == 200:
        return resp.json().get("items", [])
    return []
