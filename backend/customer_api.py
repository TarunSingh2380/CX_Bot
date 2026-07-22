"""
Ram Fincorp CRM API client.

All 5 endpoints live under a single base URL with shared api_key/api_secret auth:
  1. identify_user       — email/phone → customerID, leadID, email, mobile
  2. get_loan_details    — leadID → loan details
  3. fetch_payment_details — leadID → last N transactions
  4. validate_noc_eligibility — leadID → eligible (bool)
  5. send_noc            — leadID → sends No Dues Certificate (on-demand only)

Note: these APIs return 404/400 with a JSON body for "not found" scenarios
(e.g. no loan for this lead). That's a valid business response, not an HTTP
error — we parse the body and check `success` instead of raise_for_status().
"""

import logging
import os
from typing import Any, Dict, List, Optional

import requests

log = logging.getLogger("crm")

RAM_API_BASE_URL = os.getenv("RAM_API_BASE_URL", "").strip().rstrip("/")
RAM_API_KEY = os.getenv("RAM_API_KEY", "").strip()
RAM_API_SECRET = os.getenv("RAM_API_SECRET", "").strip()

HTTP_TIMEOUT = 20

TXN_STATUS_MAP = {
    0: "Pending",
    1: "Processing",
    2: "Success",
    3: "Failed",
    4: "Reversed",
}


def is_configured() -> bool:
    return all([RAM_API_BASE_URL, RAM_API_KEY, RAM_API_SECRET])


def _headers() -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "api_key": RAM_API_KEY,
        "api_secret": RAM_API_SECRET,
    }


def _get_json(path: str, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """
    GET an endpoint and return the parsed JSON body.
    Raises only on network/server errors (5xx, timeouts, connection failures).
    4xx responses are returned as-is since the API uses them for business logic.
    """
    resp = requests.get(
        f"{RAM_API_BASE_URL}{path}",
        headers=_headers(),
        params=params or {},
        timeout=HTTP_TIMEOUT,
    )
    if resp.status_code >= 500:
        resp.raise_for_status()
    return resp.json()


# ---- API 1: Identify User ------------------------------------------------
def identify_user(
    email: Optional[str] = None, phone: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    if not is_configured() or not (email or phone):
        return None
    try:
        params: Dict[str, str] = {}
        if email:
            params["email"] = email
        if phone:
            params["mobileNo"] = phone
        log.info("identify_user: email=%s phone=%s", email, phone)
        body = _get_json("/identify-user", params)
        if not body.get("success"):
            log.info("identify_user: %s", body.get("message", "not found"))
            return None
        data = body.get("data", {})
        log.info("identify_user: SUCCESS — customerID=%s leadID=%s", data.get("customerID"), data.get("leadID"))
        return body
    except Exception as exc:
        log.error("identify_user error: %s", exc)
        return None


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
        "email": str(data["email"]).strip() if data.get("email") else None,
        "mobile": str(data["mobile"]).strip() if data.get("mobile") else None,
    }


# ---- API 2: Get Loan Details ---------------------------------------------
def get_loan_details(lead_id: str) -> Optional[Dict[str, Any]]:
    if not is_configured() or not lead_id:
        return None
    try:
        log.info("get_loan_details: leadId=%s", lead_id)
        body = _get_json("/get-loan-details", {"leadId": lead_id})
        if not body.get("success"):
            log.info("get_loan_details: %s", body.get("message", "not found"))
            return None
        data = body.get("data") or {}
        loan = data.get("loanDetails") or data
        log.info("get_loan_details: SUCCESS — loanNo=%s status=%s", loan.get("loanNo"), loan.get("status"))
        return loan
    except Exception as exc:
        log.error("get_loan_details error: %s", exc)
        return None


# ---- API 3: Fetch Payment Details -----------------------------------------
def fetch_payment_details(lead_id: str) -> Optional[List[Dict[str, Any]]]:
    if not is_configured() or not lead_id:
        return None
    try:
        log.info("fetch_payment_details: leadId=%s", lead_id)
        body = _get_json("/fetch-payment-details", {"leadId": lead_id})
        if not body.get("success"):
            log.info("fetch_payment_details: %s", body.get("message", "not found"))
            return None
        data = body.get("data") or {}
        txns = data.get("transactions") or []
        log.info("fetch_payment_details: SUCCESS — %d transaction(s)", len(txns))
        return txns
    except Exception as exc:
        log.error("fetch_payment_details error: %s", exc)
        return None


# ---- API 4: Validate NOC Eligibility -------------------------------------
def validate_noc_eligibility(lead_id: str) -> Optional[Dict[str, Any]]:
    if not is_configured() or not lead_id:
        return None
    try:
        log.info("validate_noc_eligibility: leadId=%s", lead_id)
        body = _get_json("/validate-noc-eligibility", {"leadId": lead_id})
        if not body.get("success"):
            log.info("validate_noc_eligibility: NOT eligible — %s", body.get("message", ""))
            return {
                "eligible": False,
                "message": body.get("message", "Not eligible"),
            }
        data = body.get("data") or {}
        eligible = bool(data.get("eligible"))
        log.info("validate_noc_eligibility: SUCCESS — eligible=%s", eligible)
        return {
            "eligible": eligible,
            "message": body.get("message", ""),
        }
    except Exception as exc:
        log.error("validate_noc_eligibility error: %s", exc)
        return None


# ---- API 5: Send NOC (on-demand) -----------------------------------------
def send_noc(lead_id: str) -> Dict[str, Any]:
    if not is_configured() or not lead_id:
        return {"success": False, "message": "API not configured"}
    try:
        log.info("send_noc: leadId=%s", lead_id)
        resp = requests.post(
            f"{RAM_API_BASE_URL}/send-noc",
            headers=_headers(),
            json={"leadId": lead_id},
            timeout=HTTP_TIMEOUT,
        )
        if resp.status_code >= 500:
            resp.raise_for_status()
        body = resp.json()
        success = bool(body.get("success"))
        log.info("send_noc: %s — %s", "SUCCESS" if success else "FAILED", body.get("message", ""))
        return {
            "success": success,
            "message": body.get("message", ""),
        }
    except Exception as exc:
        log.error("send_noc error: %s", exc)
        return {"success": False, "message": str(exc)}
