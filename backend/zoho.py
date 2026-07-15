"""
Zoho Desk client for Ram Fincorp support bot.

Given a customer email, returns all OPEN tickets with their FULL thread content.

Flow:
  1. GET /contacts/search?email=...            -> contactId
  2. GET /contacts/{id}/tickets?status=Open    -> open tickets
  3. GET /tickets/{id}                          -> ticket detail (description)
  4. GET /tickets/{id}/threads                  -> thread ids
  5. GET /tickets/{id}/threads/{threadId}       -> full body per thread

Auth is OAuth2: a long-lived refresh token is exchanged for a short-lived
access token (cached until it expires). Everything is env-driven; if the Zoho
env vars are not set, the client is disabled and returns an empty result so the
rest of the app keeps working.
"""

import os
import re
import time
from typing import Any, Dict, List, Optional

import requests

# --- Configuration (from .env) --------------------------------------------
ZOHO_DC = os.getenv("ZOHO_DC", "com").strip().lstrip(".")  # com | in | eu
ZOHO_ORG_ID = os.getenv("ZOHO_ORG_ID", "").strip()
ZOHO_CLIENT_ID = os.getenv("ZOHO_CLIENT_ID", "").strip()
ZOHO_CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET", "").strip()
ZOHO_REFRESH_TOKEN = os.getenv("ZOHO_REFRESH_TOKEN", "").strip()

# Bound the fan-out so a customer with many/long tickets can't explode cost.
MAX_OPEN_TICKETS = int(os.getenv("ZOHO_MAX_OPEN_TICKETS", "5"))
MAX_THREADS_PER_TICKET = int(os.getenv("ZOHO_MAX_THREADS_PER_TICKET", "20"))
HTTP_TIMEOUT = 20  # seconds


def _api_base() -> str:
    return f"https://desk.zoho.{ZOHO_DC}/api/v1"


def _accounts_base() -> str:
    return f"https://accounts.zoho.{ZOHO_DC}/oauth/v2/token"


def is_configured() -> bool:
    return all(
        [ZOHO_ORG_ID, ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, ZOHO_REFRESH_TOKEN]
    )


# --- Access-token cache ----------------------------------------------------
_token_cache: Dict[str, Any] = {"access_token": None, "expires_at": 0.0}


def _get_access_token() -> Optional[str]:
    """Return a valid access token, refreshing it if needed."""
    now = time.time()
    if _token_cache["access_token"] and now < _token_cache["expires_at"]:
        return _token_cache["access_token"]

    resp = requests.post(
        _accounts_base(),
        params={
            "refresh_token": ZOHO_REFRESH_TOKEN,
            "client_id": ZOHO_CLIENT_ID,
            "client_secret": ZOHO_CLIENT_SECRET,
            "grant_type": "refresh_token",
        },
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"Zoho token refresh failed: {data}")
    # Refresh a minute early to avoid edge-of-expiry failures.
    _token_cache["access_token"] = token
    _token_cache["expires_at"] = now + int(data.get("expires_in", 3600)) - 60
    return token


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Zoho-oauthtoken {_get_access_token()}",
        "orgId": ZOHO_ORG_ID,
    }


def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Optional[dict]:
    """GET a Desk endpoint; None on 204/404, raises on other errors."""
    resp = requests.get(
        f"{_api_base()}{path}",
        headers=_headers(),
        params=params or {},
        timeout=HTTP_TIMEOUT,
    )
    if resp.status_code in (204, 404):
        return None
    resp.raise_for_status()
    return resp.json()


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]*\n\s*")


def _to_text(html_or_text: Optional[str]) -> str:
    """Best-effort convert an HTML thread body to readable plain text."""
    if not html_or_text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", html_or_text, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n", text, flags=re.IGNORECASE)
    text = _TAG_RE.sub("", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
    )
    return _WS_RE.sub("\n", text).strip()


# --- Public API ------------------------------------------------------------
def get_open_tickets_with_context(email: str) -> List[Dict[str, Any]]:
    """
    Return open tickets for `email`, each with full thread content:
      [{
        "ticket_id", "number", "subject", "status", "created_time",
        "description",                       # original customer message
        "threads": [{"from", "direction", "posted_time", "content"}]
      }]
    Returns [] if Zoho isn't configured or the customer/tickets aren't found.
    Never raises — failures are logged and swallowed so chat still works.
    """
    if not is_configured() or not email:
        return []

    try:
        # 1. email -> contact
        contacts = _get("/contacts/search", {"email": email})
        data = (contacts or {}).get("data") if contacts else None
        if not data:
            return []
        contact_id = data[0].get("id")
        if not contact_id:
            return []

        # 2. contact -> tickets (fetch all, filter to open on our side
        #    because Zoho rejects status= as a query param on this endpoint)
        tickets_resp = _get(
            f"/contacts/{contact_id}/tickets",
            {"limit": 50},
        )
        all_tickets = (
            (tickets_resp or {}).get("data", []) if tickets_resp else []
        )
        CLOSED_STATUSES = {"Closed", "closed", "Resolved", "resolved"}
        tickets = [
            t for t in all_tickets
            if t.get("status") not in CLOSED_STATUSES
            and t.get("statusType") != "Closed"
        ]

        results: List[Dict[str, Any]] = []
        for t in tickets[:MAX_OPEN_TICKETS]:
            ticket_id = t.get("id")
            if not ticket_id:
                continue

            # 3. ticket detail (for the original description)
            detail = _get(f"/tickets/{ticket_id}") or {}

            # 4. threads list -> full content per thread
            threads_meta = _get(
                f"/tickets/{ticket_id}/threads",
                {"limit": MAX_THREADS_PER_TICKET},
            )
            thread_list = (
                (threads_meta or {}).get("data", []) if threads_meta else []
            )

            threads: List[Dict[str, Any]] = []
            for th in thread_list[:MAX_THREADS_PER_TICKET]:
                thread_id = th.get("id")
                if not thread_id:
                    continue
                full = _get(f"/tickets/{ticket_id}/threads/{thread_id}") or {}
                body = full.get("plainText") or _to_text(full.get("content"))
                threads.append(
                    {
                        "from": (full.get("fromEmailAddress")
                                 or th.get("fromEmailAddress") or ""),
                        "direction": full.get("direction")
                        or th.get("direction")
                        or "",  # "in" (customer) / "out" (agent)
                        "posted_time": full.get("createdTime")
                        or th.get("createdTime")
                        or "",
                        "content": body,
                    }
                )

            results.append(
                {
                    "ticket_id": ticket_id,
                    "number": detail.get("ticketNumber") or t.get("ticketNumber"),
                    "subject": detail.get("subject") or t.get("subject") or "",
                    "status": detail.get("status") or t.get("status") or "",
                    "created_time": detail.get("createdTime")
                    or t.get("createdTime")
                    or "",
                    "description": _to_text(detail.get("description")),
                    "threads": threads,
                }
            )
        return results

    except Exception as exc:  # noqa: BLE001 — enrichment must never break chat
        print(f"[zoho] error fetching tickets for {email}: {exc}")
        return []
