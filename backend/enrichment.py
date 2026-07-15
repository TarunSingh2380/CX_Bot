"""
Enrichment layer.

Flow:
  1. identify_user(email/phone) → customerID, leadID, email, mobile
  2. Parallel: get_loan_details + fetch_payment_details + validate_noc_eligibility + zoho
  3. Combine into a single context string, cache for 5 min.
"""

import time
from typing import Any, Dict, List, Optional, Tuple

import customer_api
import zoho

_cache: Dict[str, Tuple[float, str, Dict[str, Optional[str]]]] = {}
CACHE_TTL = 300


def _cache_key(email: Optional[str], phone: Optional[str]) -> str:
    return f"{(email or '').lower()}|{phone or ''}"


def _format_loan(loan: Dict[str, Any]) -> str:
    if not loan:
        return "  (no loan details found)"
    lines = []
    field_map = [
        ("loanNo", "Loan No"),
        ("loanAmount", "Loan Amount"),
        ("disbursedAmount", "Disbursed Amount"),
        ("outstandingAmount", "Outstanding"),
        ("paidAmount", "Paid Amount"),
        ("status", "Status"),
        ("roi", "Rate of Interest (%)"),
        ("loanTenure", "Tenure"),
        ("disbursalDate", "Disbursal Date"),
        ("repaymentDate", "Repayment Date"),
        ("emisPaid", "EMIs Paid"),
        ("dpd", "DPD"),
        ("bounceCharges", "Bounce Charges"),
        ("penalInterest", "Penal Interest"),
        ("waiveAmount", "Waived Amount"),
        ("processingFee", "Processing Fee"),
        ("gst", "GST"),
        ("accountHolderName", "Account Holder"),
        ("accountNo", "Account No"),
        ("ifsc", "IFSC"),
        ("bankBranch", "Bank Branch"),
        ("utr", "UTR"),
        ("payoutStatus", "Payout Status"),
    ]
    for key, label in field_map:
        val = loan.get(key)
        if val is not None and str(val).strip():
            lines.append(f"  {label}: {val}")
    return "\n".join(lines) if lines else "  (no loan details found)"


def _format_transactions(txns: List[Dict[str, Any]]) -> str:
    if not txns:
        return "  (no recent transactions found)"
    lines = []
    for t in txns:
        date_raw = t.get("transactionDate") or t.get("createdAt") or "?"
        date = date_raw[:10] if isinstance(date_raw, str) else str(date_raw)
        amount = t.get("amount", "?")
        ttype = (t.get("type") or "").upper()
        mode = t.get("mode") or ""
        gateway = t.get("gateway") or ""
        status_code = t.get("status")
        status = customer_api.TXN_STATUS_MAP.get(status_code, str(status_code))
        loan_no = t.get("loanNo") or ""
        ref = t.get("referenceNo") or ""

        line = f"  - {date} | Rs {amount} | {ttype} | {mode}/{gateway} | {status}"
        if loan_no:
            line += f" | Loan: {loan_no}"
        if ref:
            line += f" | Ref: {ref}"
        lines.append(line)
    return "\n".join(lines)


def _format_noc(noc: Optional[Dict[str, Any]]) -> str:
    if noc is None:
        return "  (could not check NOC eligibility)"
    if noc.get("eligible"):
        return "  ELIGIBLE for NOC (No Dues Certificate)"
    return "  NOT eligible for NOC"


def _format_tickets(tickets: List[Dict[str, Any]]) -> str:
    if not tickets:
        return "  (no open support tickets)"
    blocks = []
    for tk in tickets:
        head = (
            f"  [Ticket #{tk.get('number') or tk.get('ticket_id')}] "
            f"{tk.get('subject', '')} — status: {tk.get('status', '')}"
        )
        parts = [head]
        if tk.get("description"):
            parts.append(f"    Original query: {tk['description']}")
        for th in tk.get("threads", []):
            who = "Customer" if th.get("direction") == "in" else "Agent"
            body = (th.get("content") or "").strip()
            if body:
                parts.append(f"    [{who}] {body}")
        blocks.append("\n".join(parts))
    return "\n\n".join(blocks)


def build_customer_context(
    email: Optional[str] = None, phone: Optional[str] = None
) -> Tuple[str, Dict[str, Optional[str]]]:
    """
    Returns (context_string, customer_data_dict).
    Fetches from all sources on cache miss; cached for CACHE_TTL seconds.
    """
    empty_data: Dict[str, Optional[str]] = {
        "customerID": None, "leadID": None, "email": None, "mobile": None,
    }
    if not (email or phone):
        return "", empty_data

    key = _cache_key(email, phone)
    now = time.time()
    cached = _cache.get(key)
    if cached and now < cached[0]:
        return cached[1], cached[2]

    # Phase 1: identify user → leadID + email
    profile = customer_api.identify_user(email=email, phone=phone)
    cust = customer_api.extract_customer_data(profile)
    lead_id = cust["leadID"]
    resolved_email = cust["email"] or email

    if not lead_id:
        _cache[key] = (now + CACHE_TTL, "", empty_data)
        return "", empty_data

    # Phase 2: parallel fetches (Python requests are blocking, but these are
    # fast API calls so sequential is fine for now; can add threading later).
    loan = customer_api.get_loan_details(lead_id)
    txns = customer_api.fetch_payment_details(lead_id)
    noc = customer_api.validate_noc_eligibility(lead_id)
    tickets = (
        zoho.get_open_tickets_with_context(resolved_email)
        if resolved_email else []
    )

    sections: List[str] = [
        "--- CUSTOMER CONTEXT (retrieved from Ram Fincorp internal systems) ---",
        "Use this to answer accurately. Do NOT invent details beyond what is "
        "shown here. If something isn't here, say you'll need to check.",
    ]

    sections.append(f"\nCustomer ID: {cust['customerID'] or 'N/A'}")
    sections.append(f"Lead ID: {cust['leadID'] or 'N/A'}")
    if cust["email"]:
        sections.append(f"Email: {cust['email']}")
    if cust["mobile"]:
        sections.append(f"Mobile: {cust['mobile']}")

    sections.append("\n--- LOAN DETAILS ---")
    sections.append(_format_loan(loan or {}))

    sections.append(f"\n--- RECENT PAYMENTS ({len(txns) if txns else 0}) ---")
    sections.append(_format_transactions(txns or []))

    sections.append("\n--- NOC ELIGIBILITY ---")
    sections.append(_format_noc(noc))

    sections.append("\n--- OPEN SUPPORT TICKETS ---")
    sections.append(_format_tickets(tickets))

    context = "\n".join(sections)
    _cache[key] = (now + CACHE_TTL, context, cust)
    return context, cust
