"""
Ram Fincorp CRM API client (async).

CRM endpoints (api_key/api_secret auth):
  1. identify_user       — email/phone → customerID, leadID, email, mobile
  2. get_loan_details    — leadID → loan details
  3. fetch_payment_details — leadID → last N transactions
  4. validate_noc_eligibility — leadID → eligible (bool)
  5. send_noc            — leadID → sends No Dues Certificate (on-demand only)

Node API endpoints (Bearer token auth):
  6. get_customer_documents — customerID → list of loan documents
  7. get_document_url       — docID → presigned download URL

CRM new endpoint:
  8. get_loan_history    — leadID + customerID → all past & present loans

Note: these APIs return 404/400 with a JSON body for "not found" scenarios
(e.g. no loan for this lead). That's a valid business response, not an HTTP
error — we parse the body and check `success` instead of raise_for_status().
"""

import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx

log = logging.getLogger("crm")

RAM_API_BASE_URL = os.getenv("RAM_API_BASE_URL", "").strip().rstrip("/")
RAM_API_KEY = os.getenv("RAM_API_KEY", "").strip()
RAM_API_SECRET = os.getenv("RAM_API_SECRET", "").strip()

RAM_NODE_API_BASE = os.getenv("RAM_NODE_API_BASE", "").strip().rstrip("/")
RAM_NODE_BEARER_TOKEN = os.getenv("RAM_NODE_BEARER_TOKEN", "").strip()

HTTP_TIMEOUT = 20.0

TXN_STATUS_MAP = {
    0: "Pending",
    1: "Processing",
    2: "Success",
    3: "Failed",
    4: "Reversed",
}

_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=HTTP_TIMEOUT)
    return _client


def log_config():
    """Log API configuration status on startup."""
    crm_ok = is_configured()
    node_ok = bool(RAM_NODE_API_BASE and RAM_NODE_BEARER_TOKEN)
    log.info("=" * 60)
    log.info("[config] CRM API configured=%s | base_url=%s", crm_ok, RAM_API_BASE_URL or "(empty)")
    log.info("[config] CRM API key_present=%s | secret_present=%s", bool(RAM_API_KEY), bool(RAM_API_SECRET))
    log.info("[config] Node API configured=%s | base_url=%s", node_ok, RAM_NODE_API_BASE or "(empty)")
    log.info("[config] Node API token_present=%s | token_len=%d", bool(RAM_NODE_BEARER_TOKEN), len(RAM_NODE_BEARER_TOKEN))
    log.info("=" * 60)


def is_configured() -> bool:
    return all([RAM_API_BASE_URL, RAM_API_KEY, RAM_API_SECRET])


def _headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "api_key": RAM_API_KEY,
        "api_secret": RAM_API_SECRET,
    }


async def _get_json(path: str, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    full_url = f"{RAM_API_BASE_URL}{path}"
    log.info("[http] GET %s | params=%s", full_url, params or {})
    start = time.time()
    resp = await _get_client().get(
        full_url,
        headers=_headers(),
        params=params or {},
    )
    elapsed = (time.time() - start) * 1000
    log.info("[http] RESPONSE %d | %s | %.0fms | body_len=%d", resp.status_code, path, elapsed, len(resp.content))
    if resp.status_code >= 500:
        log.error("[http] SERVER ERROR %d | %s | body=%s", resp.status_code, path, resp.text[:300])
        resp.raise_for_status()
    return resp.json()


# ---- API 1: Identify User ------------------------------------------------
async def identify_user(
    email: Optional[str] = None, phone: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    if not is_configured():
        log.warning("[API-1] identify_user SKIPPED — CRM not configured")
        return None
    if not (email or phone):
        log.warning("[API-1] identify_user SKIPPED — no email or phone provided")
        return None
    try:
        params: Dict[str, str] = {}
        if email:
            params["email"] = email
        if phone:
            params["mobileNo"] = phone
        log.info("[API-1] identify_user: email=%s | phone=%s", email, phone)
        body = await _get_json("/identify-user", params)
        if not body.get("success"):
            log.info("[API-1] identify_user: NOT FOUND — %s", body.get("message", "no message"))
            return None
        data = body.get("data", {})
        log.info("[API-1] identify_user: SUCCESS — customerID=%s | leadID=%s | email=%s | mobile=%s",
                 data.get("customerID"), data.get("leadID"), data.get("email"), data.get("mobile"))
        return body
    except Exception as exc:
        log.error("[API-1] identify_user: EXCEPTION — %s", exc, exc_info=True)
        return None


_JUNK_EMAIL = {"na", "n/a", "null", "none", "string", "undefined", ""}


def _clean_email(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    val = str(raw).strip()
    if val.lower() in _JUNK_EMAIL or "@" not in val:
        return None
    return val


def extract_customer_data(
    profile: Optional[Dict[str, Any]],
) -> Dict[str, Optional[str]]:
    empty = {
        "customerID": None, "leadID": None,
        "email": None, "mobile": None,
    }
    if not profile:
        return empty
    data = profile.get("data")
    if not isinstance(data, dict):
        return empty
    return {
        "customerID": str(data["customerID"]) if data.get("customerID") else None,
        "leadID": str(data["leadID"]) if data.get("leadID") else None,
        "email": _clean_email(data.get("email")),
        "mobile": str(data["mobile"]).strip() if data.get("mobile") else None,
    }


# ---- API 2: Get Loan Details ---------------------------------------------
async def get_loan_details(lead_id: str) -> Optional[Dict[str, Any]]:
    if not is_configured():
        log.warning("[API-2] get_loan_details SKIPPED — CRM not configured")
        return None
    if not lead_id:
        log.warning("[API-2] get_loan_details SKIPPED — no leadId")
        return None
    try:
        log.info("[API-2] get_loan_details: leadId=%s", lead_id)
        body = await _get_json("/get-loan-details", {"leadId": lead_id})
        if not body.get("success"):
            log.info("[API-2] get_loan_details: NOT FOUND — %s", body.get("message", "no message"))
            return None
        data = body.get("data") or {}
        loan = data.get("loanDetails") or data
        log.info("[API-2] get_loan_details: SUCCESS — loanNo=%s | status=%s | amount=%s | outstanding=%s",
                 loan.get("loanNo"), loan.get("status"), loan.get("loanAmount"), loan.get("outstandingAmount"))
        return loan
    except Exception as exc:
        log.error("[API-2] get_loan_details: EXCEPTION — %s", exc, exc_info=True)
        return None


# ---- API 3: Fetch Payment Details -----------------------------------------
async def fetch_payment_details(lead_id: str) -> Optional[List[Dict[str, Any]]]:
    if not is_configured():
        log.warning("[API-3] fetch_payment_details SKIPPED — CRM not configured")
        return None
    if not lead_id:
        log.warning("[API-3] fetch_payment_details SKIPPED — no leadId")
        return None
    try:
        log.info("[API-3] fetch_payment_details: leadId=%s", lead_id)
        body = await _get_json("/fetch-payment-details", {"leadId": lead_id})
        if not body.get("success"):
            log.info("[API-3] fetch_payment_details: NOT FOUND — %s", body.get("message", "no message"))
            return None
        data = body.get("data") or {}
        txns = data.get("transactions") or []
        log.info("[API-3] fetch_payment_details: SUCCESS — %d transaction(s)", len(txns))
        for i, t in enumerate(txns[:5]):
            log.info("[API-3]   txn[%d] date=%s | amount=%s | status=%s",
                     i, t.get("transactionDate", "?")[:10], t.get("amount"), t.get("status"))
        if len(txns) > 5:
            log.info("[API-3]   ... and %d more", len(txns) - 5)
        return txns
    except Exception as exc:
        log.error("[API-3] fetch_payment_details: EXCEPTION — %s", exc, exc_info=True)
        return None


# ---- API 4: Validate NOC Eligibility -------------------------------------
async def validate_noc_eligibility(lead_id: str) -> Optional[Dict[str, Any]]:
    if not is_configured():
        log.warning("[API-4] validate_noc_eligibility SKIPPED — CRM not configured")
        return None
    if not lead_id:
        log.warning("[API-4] validate_noc_eligibility SKIPPED — no leadId")
        return None
    try:
        log.info("[API-4] validate_noc_eligibility: leadId=%s", lead_id)
        body = await _get_json("/validate-noc-eligibility", {"leadId": lead_id})
        if not body.get("success"):
            log.info("[API-4] validate_noc_eligibility: NOT ELIGIBLE — %s", body.get("message", ""))
            return {
                "eligible": False,
                "message": body.get("message", "Not eligible"),
            }
        data = body.get("data") or {}
        eligible = bool(data.get("eligible"))
        log.info("[API-4] validate_noc_eligibility: SUCCESS — eligible=%s | message=%s", eligible, body.get("message", ""))
        return {
            "eligible": eligible,
            "message": body.get("message", ""),
        }
    except Exception as exc:
        log.error("[API-4] validate_noc_eligibility: EXCEPTION — %s", exc, exc_info=True)
        return None


# ---- API 5: Send NOC (on-demand) -----------------------------------------
async def send_noc(lead_id: str) -> Dict[str, Any]:
    if not is_configured() or not lead_id:
        log.warning("[API-5] send_noc SKIPPED — not configured or no leadId")
        return {"success": False, "message": "API not configured"}
    try:
        full_url = f"{RAM_API_BASE_URL}/send-noc"
        log.info("[API-5] send_noc: POST %s | leadId=%s", full_url, lead_id)
        start = time.time()
        resp = await _get_client().post(
            full_url,
            headers=_headers(),
            json={"leadId": lead_id},
        )
        elapsed = (time.time() - start) * 1000
        log.info("[API-5] send_noc: HTTP %d | %.0fms", resp.status_code, elapsed)
        if resp.status_code >= 500:
            resp.raise_for_status()
        body = resp.json()
        success = bool(body.get("success"))
        log.info("[API-5] send_noc: %s — %s", "SUCCESS" if success else "FAILED", body.get("message", ""))
        return {
            "success": success,
            "message": body.get("message", ""),
        }
    except Exception as exc:
        log.error("[API-5] send_noc: EXCEPTION — %s", exc, exc_info=True)
        return {"success": False, "message": str(exc)}


# ---- API 8: Get Loan History ----------------------------------------------
async def get_loan_history(lead_id: str, customer_id: str) -> Optional[List[Dict[str, Any]]]:
    if not is_configured():
        log.warning("[API-8] get_loan_history SKIPPED — CRM not configured")
        return None
    if not lead_id or not customer_id:
        log.warning("[API-8] get_loan_history SKIPPED — leadId=%s customerId=%s", lead_id, customer_id)
        return None
    try:
        log.info("[API-8] get_loan_history: leadId=%s | customerId=%s", lead_id, customer_id)
        body = await _get_json("/get-loan-history", {"leadId": lead_id, "customerId": customer_id})
        if not body.get("success"):
            log.info("[API-8] get_loan_history: NOT FOUND — %s", body.get("message", "no message"))
            return None
        loans = body.get("data") or []
        log.info("[API-8] get_loan_history: SUCCESS — %d loan(s)", len(loans))
        for i, loan in enumerate(loans):
            log.info("[API-8]   loan[%d] loanNo=%s | status=%s | amount=%s | disbursed=%s",
                     i, loan.get("loanNo"), loan.get("status"), loan.get("loanAmount"), loan.get("disbursalDate"))
        return loans
    except Exception as exc:
        log.error("[API-8] get_loan_history: EXCEPTION — %s", exc, exc_info=True)
        return None


# ---- API 6: Get Customer Documents (list) --------------------------------
async def get_customer_documents(customer_id: str, bearer_token: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
    token = bearer_token or RAM_NODE_BEARER_TOKEN
    if not RAM_NODE_API_BASE:
        log.warning("[API-6] get_customer_documents SKIPPED — RAM_NODE_API_BASE not set")
        return None
    if not token:
        log.warning("[API-6] get_customer_documents SKIPPED — no bearer token (param or env)")
        return None
    if not customer_id:
        log.warning("[API-6] get_customer_documents SKIPPED — no customerID")
        return None
    try:
        full_url = f"{RAM_NODE_API_BASE}/customers/{customer_id}/customer-loans"
        log.info("[API-6] get_customer_documents: GET %s | token_source=%s",
                 full_url, "frontend" if bearer_token else "env")
        start = time.time()
        resp = await _get_client().get(
            full_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )
        elapsed = (time.time() - start) * 1000
        log.info("[API-6] get_customer_documents: HTTP %d | %.0fms | body_len=%d",
                 resp.status_code, elapsed, len(resp.content))
        if resp.status_code >= 500:
            log.error("[API-6] get_customer_documents: SERVER ERROR %d | body=%s", resp.status_code, resp.text[:300])
            resp.raise_for_status()
        body = resp.json()
        docs = body if isinstance(body, list) else body.get("data") or body.get("documents") or []
        if not isinstance(docs, list):
            docs = [docs] if docs else []
        log.info("[API-6] get_customer_documents: SUCCESS — %d loan(s) with documents", len(docs))
        for i, loan in enumerate(docs):
            inner_docs = loan.get("documents") or []
            log.info("[API-6]   loan[%d] loanNo=%s | status=%s | %d document(s)",
                     i, loan.get("loanNo"), loan.get("status"), len(inner_docs))
        return docs
    except Exception as exc:
        log.error("[API-6] get_customer_documents: EXCEPTION — %s", exc, exc_info=True)
        return None


# ---- API 7: Get Document Presigned URL -----------------------------------
async def get_document_url(doc_id: str, bearer_token: Optional[str] = None) -> Optional[str]:
    token = bearer_token or RAM_NODE_BEARER_TOKEN
    if not RAM_NODE_API_BASE:
        log.warning("[API-7] get_document_url SKIPPED — RAM_NODE_API_BASE not set")
        return None
    if not token:
        log.warning("[API-7] get_document_url SKIPPED — no bearer token (param or env)")
        return None
    if not doc_id:
        log.warning("[API-7] get_document_url SKIPPED — no docId")
        return None
    try:
        full_url = f"{RAM_NODE_API_BASE}/customers/document/{doc_id}/presigned-url"
        log.info("[API-7] get_document_url: GET %s | token_source=%s",
                 full_url, "frontend" if bearer_token else "env")
        start = time.time()
        resp = await _get_client().get(
            full_url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )
        elapsed = (time.time() - start) * 1000
        log.info("[API-7] get_document_url: HTTP %d | %.0fms | body_len=%d",
                 resp.status_code, elapsed, len(resp.content))
        if resp.status_code >= 500:
            log.error("[API-7] get_document_url: SERVER ERROR %d | body=%s", resp.status_code, resp.text[:300])
            resp.raise_for_status()
        body = resp.json()
        if not isinstance(body, dict) or not body.get("success"):
            log.warning("[API-7] get_document_url: FAILED — message=%s", body.get("message", "unknown") if isinstance(body, dict) else "non-dict response")
            return None
        data = body.get("data") or {}
        url = data.get("presignedUrl") or data.get("url") or body.get("presignedUrl") or body.get("url")
        if url:
            log.info("[API-7] get_document_url: SUCCESS — url_length=%d", len(url))
        else:
            log.warning("[API-7] get_document_url: NO URL in response — keys=%s", list(data.keys()) if isinstance(data, dict) else "no data")
        return url
    except Exception as exc:
        log.error("[API-7] get_document_url: EXCEPTION — %s", exc, exc_info=True)
        return None
