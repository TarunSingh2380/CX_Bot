"""
Test script for all 5 Ram Fincorp CRM APIs + Zoho Desk.

Usage:
  python test_apis.py --mobileNo 9958781459
  python test_apis.py --email kanika.jain@ramfincorp.com
  python test_apis.py --mobileNo 9958781459 --skip-noc-send
"""

import argparse
import json
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

import customer_api
import zoho


def log(label: str, data, elapsed: float = 0):
    """Pretty-print a result with timing."""
    status = "OK" if data is not None else "EMPTY/FAILED"
    print(f"\n{'='*60}")
    print(f"  {label}  [{status}]  ({elapsed:.2f}s)")
    print(f"{'='*60}")
    if data is not None:
        print(json.dumps(data, indent=2, default=str, ensure_ascii=False))
    else:
        print("  (no data returned)")


def timed(fn, *args, **kwargs):
    """Call fn and return (result, elapsed_seconds)."""
    start = time.time()
    result = fn(*args, **kwargs)
    return result, time.time() - start


def main():
    parser = argparse.ArgumentParser(description="Test Ram Fincorp APIs")
    parser.add_argument("--email", type=str, help="Customer email")
    parser.add_argument("--mobileNo", type=str, help="Customer phone number")
    parser.add_argument(
        "--skip-noc-send",
        action="store_true",
        help="Skip API 5 (send-noc) to avoid actually sending a certificate",
    )
    args = parser.parse_args()

    if not args.email and not args.mobileNo:
        print("Error: provide --email or --mobileNo")
        sys.exit(1)

    print("\n" + "#" * 60)
    print("  Ram Fincorp API Test Suite")
    print("#" * 60)
    print(f"  Input email: {args.email or '(none)'}")
    print(f"  Input phone: {args.mobileNo or '(none)'}")
    print(f"  CRM configured: {customer_api.is_configured()}")
    print(f"  Zoho configured: {zoho.is_configured()}")
    print(f"  Base URL: {customer_api.RAM_API_BASE_URL}")

    if not customer_api.is_configured():
        print("\n  ERROR: RAM_API_BASE_URL / RAM_API_KEY / RAM_API_SECRET not set in .env")
        sys.exit(1)

    # --- API 1: Identify User ---
    profile, t = timed(customer_api.identify_user, email=args.email, phone=args.mobileNo)
    log("API 1 — identify_user", profile, t)

    cust = customer_api.extract_customer_data(profile)
    lead_id = cust.get("leadID")
    email = cust.get("email") or args.email

    if not lead_id:
        print("\n  STOP: No leadID returned — cannot test APIs 2-5.")
        print("  Check if the phone/email is registered in the CRM.")
        sys.exit(1)

    print(f"\n  Resolved: leadID={lead_id}, email={email}, mobile={cust.get('mobile')}")

    # --- API 2: Get Loan Details ---
    loan, t = timed(customer_api.get_loan_details, lead_id)
    log("API 2 — get_loan_details", loan, t)

    # --- API 3: Fetch Payment Details ---
    txns, t = timed(customer_api.fetch_payment_details, lead_id)
    log("API 3 — fetch_payment_details", txns, t)
    if txns:
        print(f"  ({len(txns)} transactions returned)")

    # --- API 4: Validate NOC Eligibility ---
    noc, t = timed(customer_api.validate_noc_eligibility, lead_id)
    log("API 4 — validate_noc_eligibility", noc, t)

    # --- API 5: Send NOC ---
    if args.skip_noc_send:
        print(f"\n{'='*60}")
        print("  API 5 — send_noc  [SKIPPED]  (--skip-noc-send flag)")
        print(f"{'='*60}")
    else:
        eligible = noc.get("eligible") if noc else False
        if not eligible:
            print(f"\n{'='*60}")
            print("  API 5 — send_noc  [SKIPPED]  (customer not eligible for NOC)")
            print(f"{'='*60}")
        else:
            confirm = input("\n  Customer is eligible for NOC. Send it? (y/N): ").strip().lower()
            if confirm == "y":
                result, t = timed(customer_api.send_noc, lead_id)
                log("API 5 — send_noc", result, t)
            else:
                print(f"\n{'='*60}")
                print("  API 5 — send_noc  [SKIPPED]  (user declined)")
                print(f"{'='*60}")

    # --- Zoho Desk: Open Tickets ---
    if email and zoho.is_configured():
        tickets, t = timed(zoho.get_open_tickets_with_context, email)
        log("Zoho — open tickets", tickets, t)
        if tickets:
            print(f"  ({len(tickets)} open tickets found)")
    elif not email:
        print(f"\n{'='*60}")
        print("  Zoho — open tickets  [SKIPPED]  (no email resolved)")
        print(f"{'='*60}")
    else:
        print(f"\n{'='*60}")
        print("  Zoho — open tickets  [SKIPPED]  (Zoho not configured)")
        print(f"{'='*60}")

    # --- Summary ---
    print(f"\n{'#'*60}")
    print("  Test complete!")
    print(f"{'#'*60}\n")


if __name__ == "__main__":
    main()
