"""
Zoho Desk API Test Script — Ticket Creation Flow

Tests:
  1. Token refresh (OAuth)
  2. List departments (get dept ID)
  3. Search or create contact
  4. Create a ticket
  5. Get ticket detail
  6. Get open tickets for contact

Uses .env credentials (ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, etc.)
"""

import os
import sys
import time

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

ZOHO_DC = os.getenv("ZOHO_DC", "in").strip().lstrip(".")
ZOHO_ORG_ID = os.getenv("ZOHO_ORG_ID", "").strip()
ZOHO_CLIENT_ID = os.getenv("ZOHO_CLIENT_ID", "").strip()
ZOHO_CLIENT_SECRET = os.getenv("ZOHO_CLIENT_SECRET", "").strip()
ZOHO_REFRESH_TOKEN = os.getenv("ZOHO_REFRESH_TOKEN", "").strip()

ACCOUNTS_URL = f"https://accounts.zoho.{ZOHO_DC}/oauth/v2/token"
DESK_BASE = f"https://desk.zoho.{ZOHO_DC}/api/v1"

PASS = 0
FAIL = 0
ACCESS_TOKEN = None


def header():
    print("=" * 60)
    print("  Zoho Desk API Test - Ticket Creation Flow")
    print("=" * 60)
    print(f"  DC        : {ZOHO_DC}")
    print(f"  Org ID    : {ZOHO_ORG_ID}")
    print(f"  Client ID : {ZOHO_CLIENT_ID[:20]}...")
    print(f"  Desk Base : {DESK_BASE}")
    print("=" * 60)


def result(name, ok, detail=""):
    global PASS, FAIL
    tag = "PASS" if ok else "FAIL"
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"\n  [{tag}] {name}")
    if detail:
        for line in detail.split("\n"):
            print(f"         {line}")


def auth_headers(json_content=False):
    h = {
        "Authorization": f"Zoho-oauthtoken {ACCESS_TOKEN}",
        "orgId": ZOHO_ORG_ID,
    }
    if json_content:
        h["Content-Type"] = "application/json"
    return h


# -- Test 1: Token Refresh ---------------------------------------------------
def test_token_refresh():
    global ACCESS_TOKEN
    print("\n-- Test 1: Token Refresh --")
    resp = requests.post(ACCOUNTS_URL, data={
        "grant_type": "refresh_token",
        "client_id": ZOHO_CLIENT_ID,
        "client_secret": ZOHO_CLIENT_SECRET,
        "refresh_token": ZOHO_REFRESH_TOKEN,
    }, timeout=15)
    data = resp.json()
    ACCESS_TOKEN = data.get("access_token")
    scopes = data.get("scope", "")
    if ACCESS_TOKEN:
        result("Token Refresh", True,
               f"Token: {ACCESS_TOKEN[:30]}...\nScopes: {scopes}")
    else:
        result("Token Refresh", False, f"Response: {data}")
    return bool(ACCESS_TOKEN)


# -- Test 2: List Departments ------------------------------------------------
def test_list_departments():
    print("\n-- Test 2: List Departments --")
    resp = requests.get(
        f"{DESK_BASE}/departments",
        headers=auth_headers(),
        timeout=15,
    )
    if resp.status_code != 200:
        result("List Departments", False,
               f"Status: {resp.status_code}\n{resp.text[:300]}")
        return None

    data = resp.json()
    departments = data.get("data", data) if isinstance(data, dict) else data
    if not departments:
        result("List Departments", False, "No departments found")
        return None

    dept_info = []
    first_enabled_id = None
    for d in departments:
        did = d.get("id")
        name = d.get("name", "?")
        enabled = d.get("isEnabled", False)
        dept_info.append(f"  {did} - {name} (enabled={enabled})")
        if enabled and not first_enabled_id:
            first_enabled_id = did

    result("List Departments", True,
           f"Found {len(departments)} department(s):\n" + "\n".join(dept_info) +
           f"\nUsing: {first_enabled_id}")
    return first_enabled_id


# -- Test 3: Search or Create Contact ----------------------------------------
def test_find_or_create_contact(email):
    print(f"\n-- Test 3: Search / Create Contact ({email}) --")

    # Try search first
    resp = requests.get(
        f"{DESK_BASE}/contacts/search",
        headers=auth_headers(),
        params={"email": email},
        timeout=15,
    )
    if resp.status_code == 200:
        data = resp.json().get("data", [])
        if data:
            contact = data[0]
            contact_id = contact.get("id")
            result("Find/Create Contact", True,
                   f"FOUND existing contact\n"
                   f"  ID   : {contact_id}\n"
                   f"  Name : {contact.get('firstName', '')} {contact.get('lastName', '')}\n"
                   f"  Email: {contact.get('email', '')}")
            return contact_id

    # Not found — create one
    print("  Contact not found, creating...")
    name_parts = email.split("@")[0]
    resp = requests.post(
        f"{DESK_BASE}/contacts",
        headers=auth_headers(json_content=True),
        json={
            "lastName": name_parts,
            "email": email,
        },
        timeout=15,
    )
    if resp.status_code in (200, 201):
        contact = resp.json()
        contact_id = contact.get("id")
        result("Find/Create Contact", True,
               f"CREATED new contact\n"
               f"  ID   : {contact_id}\n"
               f"  Name : {contact.get('lastName', '')}\n"
               f"  Email: {contact.get('email', '')}")
        return contact_id

    result("Find/Create Contact", False,
           f"Status: {resp.status_code}\n{resp.text[:400]}")
    return None


# -- Test 4: Create Ticket ---------------------------------------------------
def test_create_ticket(department_id, contact_id, email):
    print("\n-- Test 4: Create Ticket --")
    timestamp = int(time.time())
    payload = {
        "subject": f"[TEST] API ticket creation test - {timestamp}",
        "description": (
            "This is a test ticket created by the automated API test script.\n\n"
            "Customer query: I paid my EMI but it is not reflecting in my account.\n"
            "Category: Payment Dispute\n\n"
            "This ticket can be safely deleted."
        ),
        "departmentId": department_id,
        "contactId": contact_id,
        "priority": "Medium",
        "status": "Open",
        "email": email,
    }

    print(f"  POST {DESK_BASE}/tickets")
    print(f"  Subject   : {payload['subject']}")
    print(f"  Dept ID   : {department_id}")
    print(f"  Contact ID: {contact_id}")
    resp = requests.post(
        f"{DESK_BASE}/tickets",
        headers=auth_headers(json_content=True),
        json=payload,
        timeout=15,
    )

    if resp.status_code not in (200, 201):
        result("Create Ticket", False,
               f"Status: {resp.status_code}\n{resp.text[:500]}")
        return None

    data = resp.json()
    ticket_id = data.get("id")
    ticket_number = data.get("ticketNumber")
    result("Create Ticket", True,
           f"Ticket created!\n"
           f"  Ticket ID    : {ticket_id}\n"
           f"  Ticket Number: {ticket_number}\n"
           f"  Status       : {data.get('status')}\n"
           f"  Priority     : {data.get('priority')}\n"
           f"  Department   : {data.get('departmentId')}\n"
           f"  Subject      : {data.get('subject')}")
    return {"id": ticket_id, "number": ticket_number}


# -- Test 5: Get Ticket Detail -----------------------------------------------
def test_get_ticket_detail(ticket_id):
    print(f"\n-- Test 5: Get Ticket Detail --")
    resp = requests.get(
        f"{DESK_BASE}/tickets/{ticket_id}",
        headers=auth_headers(),
        timeout=15,
    )
    if resp.status_code != 200:
        result("Get Ticket Detail", False,
               f"Status: {resp.status_code}\n{resp.text[:300]}")
        return False

    data = resp.json()
    info = (
        f"Ticket Number : {data.get('ticketNumber')}\n"
        f"Subject       : {data.get('subject')}\n"
        f"Status        : {data.get('status')}\n"
        f"Priority      : {data.get('priority')}\n"
        f"Category      : {data.get('category')}\n"
        f"Department    : {data.get('departmentId')}\n"
        f"Created       : {data.get('createdTime')}\n"
        f"Contact Email : {data.get('email')}\n"
        f"Contact ID    : {data.get('contactId')}"
    )
    result("Get Ticket Detail", True, info)
    return True


# -- Test 6: Get Open Tickets for Contact ------------------------------------
def test_get_open_tickets(contact_id, expected_ticket_number=None):
    print(f"\n-- Test 6: Get Open Tickets for Contact --")
    resp = requests.get(
        f"{DESK_BASE}/contacts/{contact_id}/tickets",
        headers=auth_headers(),
        params={"limit": 20},
        timeout=15,
    )
    if resp.status_code in (204, 404):
        result("Get Open Tickets", False, "No tickets found")
        return False

    if resp.status_code != 200:
        result("Get Open Tickets", False,
               f"Status: {resp.status_code}\n{resp.text[:300]}")
        return False

    tickets = resp.json().get("data", [])
    open_tickets = [t for t in tickets if t.get("status") not in ("Closed", "Resolved")]

    info = f"Total: {len(tickets)}, Open: {len(open_tickets)}"
    found = True
    for t in open_tickets[:5]:
        marker = ""
        if expected_ticket_number and str(t.get("ticketNumber")) == str(expected_ticket_number):
            marker = " <-- JUST CREATED"
        info += f"\n  #{t.get('ticketNumber')} - {t.get('subject', '')[:50]} [{t.get('status')}]{marker}"

    if expected_ticket_number:
        found = any(str(t.get("ticketNumber")) == str(expected_ticket_number) for t in open_tickets)
        if not found:
            info += f"\n  Expected #{expected_ticket_number}: NOT FOUND in open list"

    result("Get Open Tickets", found, info)
    return found


# -- Main ---------------------------------------------------------------------
def main():
    header()
    test_email = "tarunsingh9216@gmail.com"

    # 1. Token
    if not test_token_refresh():
        print("\nToken refresh failed - cannot continue.")
        print(f"\nResults: {PASS} passed, {FAIL} failed out of {PASS + FAIL}")
        return

    # 2. List departments
    dept_id = test_list_departments()
    if not dept_id:
        print("\nNo departments found - cannot create ticket.")
        print(f"\nResults: {PASS} passed, {FAIL} failed out of {PASS + FAIL}")
        return

    # 3. Find or create contact
    contact_id = test_find_or_create_contact(test_email)
    if not contact_id:
        print("\nCould not find or create contact - cannot create ticket.")
        print(f"\nResults: {PASS} passed, {FAIL} failed out of {PASS + FAIL}")
        return

    # 4. Create ticket
    ticket = test_create_ticket(dept_id, contact_id, test_email)
    if not ticket:
        print("\nTicket creation FAILED.")
        print(f"\nResults: {PASS} passed, {FAIL} failed out of {PASS + FAIL}")
        return

    # 5. Get ticket detail
    test_get_ticket_detail(ticket["id"])

    # 6. Get open tickets for contact
    test_get_open_tickets(contact_id, expected_ticket_number=ticket["number"])

    # Summary
    total = PASS + FAIL
    print("\n" + "=" * 60)
    print(f"  Results: {PASS}/{total} passed, {FAIL}/{total} failed")
    print("=" * 60)


if __name__ == "__main__":
    main()
