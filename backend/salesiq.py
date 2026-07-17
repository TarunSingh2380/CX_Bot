"""
Zoho SalesIQ integration for live agent handoff.

Uses dual OAuth:
  - Org OAuth (1005.x) for v1 visitor APIs (open conversation, send visitor msg)
  - Regular OAuth (1000.x) for v2 operator APIs (get details, get messages, close)
"""

import logging
import os
import time
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger("salesiq")

ZOHO_DC = os.getenv("ZOHO_DC", "in")
SCREEN_NAME = os.getenv("SALESIQ_SCREEN_NAME", "")
APP_ID = os.getenv("SALESIQ_APP_ID", "")
HUMAN_DEPT_ID = "219419000000002012"

ORG_CLIENT_ID = os.getenv("SALESIQ_ORG_CLIENT_ID", "")
ORG_CLIENT_SECRET = os.getenv("SALESIQ_ORG_CLIENT_SECRET", "")
ORG_REFRESH_TOKEN = os.getenv("SALESIQ_ORG_REFRESH_TOKEN", "")

V2_CLIENT_ID = os.getenv("SALESIQ_V2_CLIENT_ID", "")
V2_CLIENT_SECRET = os.getenv("SALESIQ_V2_CLIENT_SECRET", "")
V2_REFRESH_TOKEN = os.getenv("SALESIQ_V2_REFRESH_TOKEN", "")

ACCOUNTS_URL = f"https://accounts.zoho.{ZOHO_DC}/oauth/v2/token"
SALESIQ_BASE = f"https://salesiq.zoho.{ZOHO_DC}"

_token_cache: Dict[str, tuple] = {}


def _get_token(client_id: str, client_secret: str, refresh_token: str, key: str) -> str:
    cached = _token_cache.get(key)
    if cached and time.time() < cached[1]:
        log.debug("[token] Using cached %s token", key)
        return cached[0]

    log.info("[token] Refreshing %s token...", key)
    resp = requests.post(ACCOUNTS_URL, data={
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }, timeout=10)
    data = resp.json()
    token = data.get("access_token", "")
    if not token:
        log.error("[token] Failed to get %s token: %s", key, data)
        return ""
    expires_in = data.get("expires_in_sec", data.get("expires_in", 3600))
    _token_cache[key] = (token, time.time() + expires_in - 60)
    log.info("[token] %s token refreshed, expires in %ss", key, expires_in)
    return token


def _org_token() -> str:
    return _get_token(ORG_CLIENT_ID, ORG_CLIENT_SECRET, ORG_REFRESH_TOKEN, "org")


def _v2_token() -> str:
    return _get_token(V2_CLIENT_ID, V2_CLIENT_SECRET, V2_REFRESH_TOKEN, "v2")


def _v1_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Zoho-oauthtoken {_org_token()}",
        "Content-Type": "application/json",
    }


def _v2_headers(json: bool = False) -> Dict[str, str]:
    h: Dict[str, str] = {"Authorization": f"Zoho-oauthtoken {_v2_token()}"}
    if json:
        h["Content-Type"] = "application/json"
    return h


def open_conversation(
    name: str, email: str, phone: str, question: str
) -> Optional[str]:
    """Create a SalesIQ conversation. Returns conversation_id or None."""
    url = f"{SALESIQ_BASE}/api/visitor/v1/{SCREEN_NAME}/conversations"
    payload = {
        "app_id": APP_ID,
        "department_id": HUMAN_DEPT_ID,
        "question": question,
        "visitor": {
            "user_id": f"customer-{email or phone}",
            "name": name,
            "email": email or "",
            "phone": phone or "",
        },
    }
    log.info("[open_conversation] Creating chat for %s (%s)", name, email or phone)
    log.debug("[open_conversation] POST %s | payload: %s", url, payload)
    try:
        resp = requests.post(url, json=payload, headers=_v1_headers(), timeout=10)
        log.info("[open_conversation] Status: %s", resp.status_code)
        body = resp.json()
        data = body.get("data", {})
        conv_id = data.get("conversation_id") or data.get("id")
        if conv_id:
            log.info("[open_conversation] SUCCESS — conversation_id: %s", conv_id)
        else:
            log.error("[open_conversation] FAILED — response: %s", body)
        return conv_id
    except Exception as e:
        log.error("[open_conversation] Exception: %s", e)
        return None


def send_visitor_message(conversation_id: str, text: str) -> bool:
    """Send a message as the visitor/customer."""
    url = f"{SALESIQ_BASE}/api/visitor/v1/{SCREEN_NAME}/conversations/{conversation_id}/messages"
    log.info("[send_visitor_msg] conv=%s | msg=%s", conversation_id, text[:80])
    try:
        resp = requests.post(url, json={"text": text}, headers=_v1_headers(), timeout=10)
        log.info("[send_visitor_msg] Status: %s", resp.status_code)
        if resp.status_code in (200, 204):
            log.info("[send_visitor_msg] SUCCESS")
            return True
        log.error("[send_visitor_msg] FAILED — %s", resp.text[:200])
        return False
    except Exception as e:
        log.error("[send_visitor_msg] Exception: %s", e)
        return False


def get_messages(conversation_id: str) -> List[Dict[str, Any]]:
    """Fetch all messages in a conversation."""
    url = f"{SALESIQ_BASE}/api/v2/{SCREEN_NAME}/conversations/{conversation_id}/messages"
    log.debug("[get_messages] conv=%s", conversation_id)
    try:
        resp = requests.get(url, headers=_v2_headers(), timeout=10)
        messages = resp.json().get("data", [])
        log.debug("[get_messages] Got %d messages", len(messages))
        return messages
    except Exception as e:
        log.error("[get_messages] Exception: %s", e)
        return []


def get_conversation_status(conversation_id: str) -> Optional[str]:
    """Get conversation status: Connected, Waiting, Closed, etc."""
    url = f"{SALESIQ_BASE}/api/v2/{SCREEN_NAME}/conversations/{conversation_id}"
    log.debug("[get_status] conv=%s", conversation_id)
    try:
        resp = requests.get(url, headers=_v2_headers(), timeout=10)
        data = resp.json().get("data", {})
        status = data.get("status")
        log.debug("[get_status] Status: %s", status)
        return status
    except Exception as e:
        log.error("[get_status] Exception: %s", e)
        return None


def close_conversation(conversation_id: str) -> bool:
    """Close a conversation."""
    url = f"{SALESIQ_BASE}/api/v2/{SCREEN_NAME}/conversations/{conversation_id}/close"
    log.info("[close_conversation] conv=%s", conversation_id)
    try:
        resp = requests.put(url, headers=_v2_headers(), timeout=10)
        log.info("[close_conversation] Status: %s", resp.status_code)
        if resp.status_code in (200, 204):
            log.info("[close_conversation] SUCCESS")
            return True
        log.error("[close_conversation] FAILED — %s", resp.text[:200])
        return False
    except Exception as e:
        log.error("[close_conversation] Exception: %s", e)
        return False
