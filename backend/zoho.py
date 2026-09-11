"""
Zoho Desk client for Ram Fincorp support bot (async).

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

import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger("zoho")

# --- Configuration (from .env) --------------------------------------------
ZOHO_DC = os.getenv("ZOHO_DC", "com").strip().lstrip(".")  # com | in | eu
ZOHO_ORG_ID = os.getenv("ZOHO_ORG_ID", "").strip()
ZOHO_CLIENT_ID = os.getenv("ZOHO_CLIENT_ID", "").strip()
ZOHO_CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET", "").strip()
ZOHO_REFRESH_TOKEN = os.getenv("ZOHO_REFRESH_TOKEN", "").strip()

# Bound the fan-out so a customer with many/long tickets can't explode cost.
DESK_DEPARTMENT_ID = os.getenv("ZOHO_DESK_DEPARTMENT_ID", "").strip()
DESK_ASSIGNEE_ID = os.getenv("ZOHO_DESK_ASSIGNEE_ID", "").strip()
MAX_OPEN_TICKETS = int(os.getenv("ZOHO_MAX_OPEN_TICKETS", "5"))
MAX_THREADS_PER_TICKET = int(os.getenv("ZOHO_MAX_THREADS_PER_TICKET", "20"))
HTTP_TIMEOUT = 20.0

_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=HTTP_TIMEOUT)
    return _client


def log_config():
    """Log Zoho configuration status on startup."""
    configured = is_configured()
    log.info("=" * 60)
    log.info("[config] Zoho Desk configured=%s | dc=%s | orgId=%s",
             configured, ZOHO_DC, ZOHO_ORG_ID or "(empty)")
    log.info("[config] Zoho client_id_present=%s | client_secret_present=%s | refresh_token_present=%s",
             bool(ZOHO_CLIENT_ID), bool(ZOHO_CLIENT_SECRET), bool(ZOHO_REFRESH_TOKEN))
    log.info("[config] Zoho department_id=%s | assignee_id=%s",
             DESK_DEPARTMENT_ID or "(empty)", DESK_ASSIGNEE_ID or "(empty)")
    log.info("[config] Zoho max_open_tickets=%d | max_threads_per_ticket=%d",
             MAX_OPEN_TICKETS, MAX_THREADS_PER_TICKET)
    log.info("=" * 60)


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


async def _get_access_token() -> Optional[str]:
    """Return a valid access token, refreshing it if needed."""
    now = time.time()
    if _token_cache["access_token"] and now < _token_cache["expires_at"]:
        log.debug("[zoho-auth] Using cached token | expires_in=%.0fs", _token_cache["expires_at"] - now)
        return _token_cache["access_token"]

    url = _accounts_base()
    log.info("[zoho-auth] Refreshing access token | POST %s", url)
    start = time.time()
    resp = await _get_client().post(
        url,
        params={
            "refresh_token": ZOHO_REFRESH_TOKEN,
            "client_id": ZOHO_CLIENT_ID,
            "client_secret": ZOHO_CLIENT_SECRET,
            "grant_type": "refresh_token",
        },
    )
    elapsed = (time.time() - start) * 1000
    log.info("[zoho-auth] Token refresh HTTP %d | %.0fms", resp.status_code, elapsed)
    resp.raise_for_status()
    data = resp.json()
    token = data.get("access_token")
    if not token:
        log.error("[zoho-auth] Token refresh FAILED — no access_token in response: %s", str(data)[:200])
        raise RuntimeError(f"Zoho token refresh failed: {data}")
    _token_cache["access_token"] = token
    _token_cache["expires_at"] = now + int(data.get("expires_in", 3600)) - 60
    log.info("[zoho-auth] Token refreshed — expires_in=%ss", data.get("expires_in", "?"))
    return token


async def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Zoho-oauthtoken {await _get_access_token()}",
        "orgId": ZOHO_ORG_ID,
    }


async def _get(path: str, params: Optional[Dict[str, Any]] = None) -> Optional[dict]:
    """GET a Desk endpoint; None on 204/404, raises on other errors."""
    full_url = f"{_api_base()}{path}"
    log.info("[zoho-http] GET %s | params=%s", full_url, params or {})
    headers = await _headers()
    start = time.time()
    resp = await _get_client().get(
        full_url,
        headers=headers,
        params=params or {},
    )
    elapsed = (time.time() - start) * 1000
    log.info("[zoho-http] RESPONSE %d | %s | %.0fms | body_len=%d",
             resp.status_code, path, elapsed, len(resp.content))
    if resp.status_code in (204, 404):
        log.info("[zoho-http] %d for %s — returning None", resp.status_code, path)
        return None
    if resp.status_code >= 400:
        log.error("[zoho-http] ERROR %d | %s | body=%s", resp.status_code, path, resp.text[:300])
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
async def get_open_tickets_with_context(email: str) -> List[Dict[str, Any]]:
    """
    Return open tickets for `email`, each with full thread content.
    """
    if not is_configured():
        log.warning("[zoho-tickets] SKIPPED — Zoho not configured")
        return []
    if not email:
        log.warning("[zoho-tickets] SKIPPED — no email provided")
        return []

    total_start = time.time()
    log.info("[zoho-tickets] Fetching tickets for email=%s", email)

    try:
        # 1. email -> contact
        log.info("[zoho-tickets] Step 1: search contact by email=%s", email)
        contacts = await _get("/contacts/search", {"email": email})
        data = (contacts or {}).get("data") if contacts else None
        if not data:
            log.info("[zoho-tickets] No contact found for %s", email)
            return []
        contact_id = data[0].get("id")
        if not contact_id:
            log.info("[zoho-tickets] Contact has no ID for %s", email)
            return []
        log.info("[zoho-tickets] Contact found: id=%s | name=%s",
                 contact_id, data[0].get("lastName", "?"))

        # 2. contact -> tickets
        log.info("[zoho-tickets] Step 2: fetch tickets for contact=%s", contact_id)
        tickets_resp = await _get(
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
        log.info("[zoho-tickets] Found %d total tickets, %d open (after filtering closed)",
                 len(all_tickets), len(tickets))

        results: List[Dict[str, Any]] = []
        for idx, t in enumerate(tickets[:MAX_OPEN_TICKETS]):
            ticket_id = t.get("id")
            if not ticket_id:
                continue

            log.info("[zoho-tickets] Step 3: fetching detail for ticket[%d] id=%s subject='%s'",
                     idx, ticket_id, (t.get("subject") or "")[:60])

            # 3. ticket detail (for the original description)
            detail = await _get(f"/tickets/{ticket_id}") or {}

            # 4. threads list -> full content per thread
            log.info("[zoho-tickets] Step 4: fetching threads for ticket=%s", ticket_id)
            threads_meta = await _get(
                f"/tickets/{ticket_id}/threads",
                {"limit": MAX_THREADS_PER_TICKET},
            )
            thread_list = (
                (threads_meta or {}).get("data", []) if threads_meta else []
            )
            log.info("[zoho-tickets] Ticket %s has %d thread(s)", ticket_id, len(thread_list))

            threads: List[Dict[str, Any]] = []
            for th in thread_list[:MAX_THREADS_PER_TICKET]:
                thread_id = th.get("id")
                if not thread_id:
                    continue
                full = await _get(f"/tickets/{ticket_id}/threads/{thread_id}") or {}
                body = full.get("plainText") or _to_text(full.get("content"))
                threads.append(
                    {
                        "from": (full.get("fromEmailAddress")
                                 or th.get("fromEmailAddress") or ""),
                        "direction": full.get("direction")
                        or th.get("direction")
                        or "",
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
            log.info("[zoho-tickets] Ticket #%s: %d thread(s) loaded",
                     results[-1]["number"], len(threads))

        total_elapsed = (time.time() - total_start) * 1000
        log.info("[zoho-tickets] DONE — %d open tickets fetched in %.0fms for %s",
                 len(results), total_elapsed, email)
        return results

    except Exception as exc:
        total_elapsed = (time.time() - total_start) * 1000
        log.error("[zoho-tickets] EXCEPTION fetching tickets for %s in %.0fms: %s",
                  email, total_elapsed, exc, exc_info=True)
        return []


async def find_or_create_contact(email: str) -> Optional[str]:
    """Search for a Desk contact by email; create one if not found."""
    if not is_configured() or not email:
        log.warning("[zoho-contact] SKIPPED — not configured or no email")
        return None
    try:
        log.info("[zoho-contact] Searching contact for email=%s", email)
        result = await _get("/contacts/search", {"email": email})
        if result and result.get("data"):
            contact_id = result["data"][0].get("id")
            log.info("[zoho-contact] Contact FOUND for %s: id=%s", email, contact_id)
            return contact_id

        log.info("[zoho-contact] Contact NOT FOUND for %s — creating...", email)
        url = f"{_api_base()}/contacts"
        payload = {"lastName": email.split("@")[0], "email": email}
        log.info("[zoho-contact] POST %s | payload=%s", url, payload)
        headers = await _headers()
        start = time.time()
        resp = await _get_client().post(
            url,
            headers={**headers, "Content-Type": "application/json"},
            json=payload,
        )
        elapsed = (time.time() - start) * 1000
        log.info("[zoho-contact] Create response: HTTP %d | %.0fms", resp.status_code, elapsed)
        if resp.status_code in (200, 201):
            contact_id = resp.json().get("id")
            log.info("[zoho-contact] Contact CREATED: id=%s for %s", contact_id, email)
            return contact_id
        log.error("[zoho-contact] Contact creation FAILED: HTTP %d | body=%s",
                  resp.status_code, resp.text[:200])
        return None
    except Exception as exc:
        log.error("[zoho-contact] EXCEPTION for %s: %s", email, exc, exc_info=True)
        return None


async def create_ticket(
    contact_id: str,
    email: str,
    subject: str,
    description: str,
    category: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Create a Zoho Desk ticket. Returns {id, number, status} or None."""
    if not is_configured() or not contact_id:
        log.warning("[zoho-ticket-create] SKIPPED — not configured or no contactId")
        return None
    dept = DESK_DEPARTMENT_ID
    if not dept:
        log.error("[zoho-ticket-create] ZOHO_DESK_DEPARTMENT_ID not set")
        return None
    try:
        payload: Dict[str, Any] = {
            "subject": subject[:255],
            "description": description,
            "departmentId": dept,
            "contactId": contact_id,
            "email": email,
            "priority": "Medium",
            "status": "Open",
            "channel": "Web",
        }
        if DESK_ASSIGNEE_ID:
            payload["assigneeId"] = DESK_ASSIGNEE_ID
        url = f"{_api_base()}/tickets"
        log.info("[zoho-ticket-create] POST %s | subject='%s' | email=%s | contactId=%s",
                 url, subject[:80], email, contact_id)
        headers = await _headers()
        start = time.time()
        resp = await _get_client().post(
            url,
            headers={**headers, "Content-Type": "application/json"},
            json=payload,
        )
        elapsed = (time.time() - start) * 1000
        log.info("[zoho-ticket-create] HTTP %d | %.0fms", resp.status_code, elapsed)
        if resp.status_code in (200, 201):
            data = resp.json()
            ticket = {
                "id": data.get("id"),
                "number": data.get("ticketNumber"),
                "status": data.get("status"),
            }
            log.info("[zoho-ticket-create] SUCCESS — ticket #%s (id=%s) created for %s",
                     ticket["number"], ticket["id"], email)
            return ticket
        log.error("[zoho-ticket-create] FAILED — HTTP %d | body=%s",
                  resp.status_code, resp.text[:300])
        return None
    except Exception as exc:
        log.error("[zoho-ticket-create] EXCEPTION: %s", exc, exc_info=True)
        return None
