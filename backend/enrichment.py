"""
Enrichment layer (async).

Flow:
  1. identify_user(email/phone) → customerID, leadID, email, mobile
  2. Parallel via asyncio.gather: get_loan_details + fetch_payment_details
     + validate_noc_eligibility + get_loan_history + get_customer_documents + zoho
  3. Combine into a single context string, cache for 5 min.
"""

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

import customer_api
import zoho

log = logging.getLogger("enrichment")

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


def _format_loan_history(loans: List[Dict[str, Any]]) -> str:
    if not loans:
        return "  (no loan history found)"
    lines = []
    for loan in loans:
        loan_no = loan.get("loanNo") or "Unknown"
        status = loan.get("status") or ""
        amount = loan.get("loanAmount") or ""
        disbursal = loan.get("disbursalDate") or ""
        repayment = loan.get("repaymentDate") or ""
        collected = loan.get("collectedDate") or ""
        tenure = loan.get("tenure") or ""
        roi = loan.get("roi") or ""
        lead_type = loan.get("leadType") or ""
        product_type = loan.get("productType") or ""
        line = f"  Loan {loan_no} — Status: {status}"
        if amount:
            line += f" | Amount: Rs {amount}"
        if disbursal:
            line += f" | Disbursed: {disbursal}"
        if repayment:
            line += f" | Repayment: {repayment}"
        if collected:
            collected_str = str(collected)[:10] if isinstance(collected, str) else str(collected)
            line += f" | Collected: {collected_str}"
        if tenure:
            line += f" | Tenure: {tenure} days"
        if roi:
            line += f" | ROI: {roi}%"
        if product_type:
            line += f" | Type: {product_type}"
        if lead_type:
            line += f" | {lead_type}"
        lines.append(line)
    return "\n".join(lines)


def _format_documents(loans: List[Dict[str, Any]]) -> str:
    if not loans:
        return "  (no loan documents found)"
    lines = []
    for loan in loans:
        loan_no = loan.get("loanNo") or "Unknown"
        status = loan.get("status") or loan.get("loanStatus") or ""
        amount = loan.get("disbursalAmount") or loan.get("loanAmtApproved") or ""
        disbursal = loan.get("disbursalDate") or ""
        header = f"  Loan {loan_no} — Status: {status}"
        if amount:
            header += f" | Amount: Rs {amount}"
        if disbursal:
            header += f" | Disbursed: {disbursal}"
        lines.append(header)
        inner_docs = loan.get("documents") or []
        for doc in inner_docs:
            doc_id = doc.get("documentID") or ""
            doc_type = doc.get("type") or doc.get("documentType") or "Document"
            lines.append(f"    - {doc_type} (ID: {doc_id})")
        if not inner_docs:
            lines.append("    (no documents)")
    return "\n".join(lines)


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


async def _timed_call(label: str, coro):
    """Await a coroutine with timing and logging. Returns (result, elapsed_ms)."""
    log.info("[enrich] >>> calling %s", label)
    start = time.time()
    try:
        result = await coro
        elapsed = (time.time() - start) * 1000
        log.info("[enrich] <<< %s completed in %.0fms | result=%s",
                 label, elapsed, "OK" if result else "EMPTY")
        return result, elapsed
    except Exception as exc:
        elapsed = (time.time() - start) * 1000
        log.error("[enrich] <<< %s FAILED in %.0fms | error=%s", label, elapsed, exc)
        return None, elapsed


async def build_customer_context(
    email: Optional[str] = None, phone: Optional[str] = None,
    bearer_token: Optional[str] = None,
) -> Tuple[str, Dict[str, Optional[str]]]:
    """
    Returns (context_string, customer_data_dict).
    Fetches from all sources on cache miss; cached for CACHE_TTL seconds.
    """
    empty_data: Dict[str, Optional[str]] = {
        "customerID": None, "leadID": None, "email": None, "mobile": None,
    }
    if not (email or phone):
        log.info("[enrich] build_customer_context SKIPPED — no email or phone")
        return "", empty_data

    key = _cache_key(email, phone)
    now = time.time()
    cached = _cache.get(key)
    if cached and now < cached[0]:
        ttl_left = cached[0] - now
        log.info("[enrich] CACHE HIT for %s | ttl_remaining=%.0fs | context_len=%d",
                 key, ttl_left, len(cached[1]))
        return cached[1], cached[2]

    total_start = time.time()
    log.info("=" * 60)
    log.info("[enrich] CACHE MISS — building context for email=%s phone=%s", email, phone)

    # Phase 1: identify user (must run first — need leadID for everything else)
    profile, t1 = await _timed_call(
        "API-1 identify_user",
        customer_api.identify_user(email=email, phone=phone),
    )
    cust = customer_api.extract_customer_data(profile)
    lead_id = cust["leadID"]
    resolved_email = cust["email"] or email

    if not lead_id:
        total_elapsed = (time.time() - total_start) * 1000
        log.info("[enrich] Customer NOT FOUND — total time %.0fms", total_elapsed)
        log.info("=" * 60)
        _cache[key] = (now + CACHE_TTL, "", empty_data)
        return "", empty_data

    log.info("[enrich] Customer identified — customerID=%s | leadID=%s | email=%s | mobile=%s",
             cust["customerID"], lead_id, cust["email"], cust["mobile"])

    # Phase 2: fetch all remaining APIs in PARALLEL via asyncio.gather
    log.info("[enrich] Starting parallel fetch (asyncio.gather) for 6 APIs...")
    parallel_start = time.time()

    coros = [
        _timed_call("API-2 get_loan_details", customer_api.get_loan_details(lead_id)),
        _timed_call("API-3 fetch_payment_details", customer_api.fetch_payment_details(lead_id)),
        _timed_call("API-4 validate_noc_eligibility", customer_api.validate_noc_eligibility(lead_id)),
        _timed_call("API-8 get_loan_history", customer_api.get_loan_history(lead_id, cust["customerID"])),
        _timed_call("API-6 get_customer_documents", customer_api.get_customer_documents(cust["customerID"], bearer_token)),
    ]
    if resolved_email:
        coros.append(_timed_call("Zoho get_open_tickets", zoho.get_open_tickets_with_context(resolved_email)))

    all_results = await asyncio.gather(*coros)

    (loan, t2) = all_results[0]
    (txns, t3) = all_results[1]
    (noc, t4) = all_results[2]
    (loan_history, t5) = all_results[3]
    (docs, t6) = all_results[4]
    if resolved_email and len(all_results) > 5:
        (tickets, t7) = all_results[5]
    else:
        tickets, t7 = [], 0
        log.info("[enrich] Zoho tickets SKIPPED — no email")

    parallel_elapsed = (time.time() - parallel_start) * 1000
    total_elapsed = (time.time() - total_start) * 1000
    sequential_sum = t2 + t3 + t4 + t5 + t6 + t7
    log.info("-" * 40)
    log.info("[enrich] TIMING SUMMARY (PARALLEL — asyncio.gather):")
    log.info("[enrich]   API-1 identify_user      : %.0fms (sequential — must run first)", t1)
    log.info("[enrich]   API-2 loan_details        : %.0fms", t2)
    log.info("[enrich]   API-3 payment_details     : %.0fms", t3)
    log.info("[enrich]   API-4 noc_eligibility     : %.0fms", t4)
    log.info("[enrich]   API-8 loan_history        : %.0fms", t5)
    log.info("[enrich]   API-6 customer_documents  : %.0fms", t6)
    log.info("[enrich]   Zoho  open_tickets        : %.0fms", t7)
    log.info("[enrich]   Parallel wall-clock       : %.0fms (saved %.0fms vs sequential %.0fms)",
             parallel_elapsed, sequential_sum - parallel_elapsed, sequential_sum)
    log.info("[enrich]   TOTAL (incl. API-1)       : %.0fms", total_elapsed)
    log.info("-" * 40)
    log.info("[enrich] DATA SUMMARY: loan=%s | txns=%d | noc=%s | history=%d | docs=%d | tickets=%d",
             "yes" if loan else "no", len(txns or []),
             "yes" if noc else "no", len(loan_history or []),
             len(docs or []), len(tickets or []))

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

    sections.append(f"\n--- LOAN HISTORY ({len(loan_history) if loan_history else 0}) ---")
    sections.append(_format_loan_history(loan_history or []))

    sections.append("\n--- NOC ELIGIBILITY ---")
    sections.append(_format_noc(noc))

    sections.append(f"\n--- CUSTOMER DOCUMENTS ({len(docs) if docs else 0}) ---")
    sections.append(_format_documents(docs or []))

    sections.append("\n--- OPEN SUPPORT TICKETS ---")
    sections.append(_format_tickets(tickets or []))

    context = "\n".join(sections)
    _cache[key] = (now + CACHE_TTL, context, cust)
    log.info("[enrich] Context built — %d chars, cached for %ds", len(context), CACHE_TTL)
    log.info("=" * 60)
    return context, cust
