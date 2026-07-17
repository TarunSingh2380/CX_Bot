# SalesIQ Live Flow Test
# Simulates: Customer msg → Agent replies a number → Backend reads it → Multiplies by 10 → Sends back
# Usage: python salesiq_test_api.py

import os
import sys
import time
import requests
from dotenv import load_dotenv

load_dotenv()

ZOHO_DC = os.getenv("ZOHO_DC", "in")
SCREEN_NAME = os.getenv("SALESIQ_SCREEN_NAME")
APP_ID = os.getenv("SALESIQ_APP_ID")
HUMAN_DEPT_ID = "219419000000002012"

ORG_CLIENT_ID = os.getenv("SALESIQ_ORG_CLIENT_ID")
ORG_CLIENT_SECRET = os.getenv("SALESIQ_ORG_CLIENT_SECRET")
ORG_REFRESH_TOKEN = os.getenv("SALESIQ_ORG_REFRESH_TOKEN")

V2_CLIENT_ID = os.getenv("SALESIQ_V2_CLIENT_ID")
V2_CLIENT_SECRET = os.getenv("SALESIQ_V2_CLIENT_SECRET")
V2_REFRESH_TOKEN = os.getenv("SALESIQ_V2_REFRESH_TOKEN")

ACCOUNTS_URL = f"https://accounts.zoho.{ZOHO_DC}/oauth/v2/token"
SALESIQ_BASE = f"https://salesiq.zoho.{ZOHO_DC}"


def get_token(client_id, client_secret, refresh_token):
    resp = requests.post(ACCOUNTS_URL, data={
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    })
    return resp.json().get("access_token")


def hdrs(token):
    return {
        "Authorization": f"Zoho-oauthtoken {token}",
        "Content-Type": "application/json",
    }


print("\n" + "=" * 60)
print("   SALESIQ LIVE FLOW TEST")
print("   Customer → Agent (number) → Backend (×10) → Reply")
print("=" * 60)

# Step 1: Get tokens
print("\n[1] Getting tokens...")
org_token = get_token(ORG_CLIENT_ID, ORG_CLIENT_SECRET, ORG_REFRESH_TOKEN)
v2_token = get_token(V2_CLIENT_ID, V2_CLIENT_SECRET, V2_REFRESH_TOKEN)
if not org_token or not v2_token:
    print("  ERROR: Could not get tokens")
    sys.exit(1)
print("  Both tokens acquired.")

# Step 2: Open conversation
print("\n[2] Opening conversation...")
resp = requests.post(
    f"{SALESIQ_BASE}/api/visitor/v1/{SCREEN_NAME}/conversations",
    json={
        "app_id": APP_ID,
        "department_id": HUMAN_DEPT_ID,
        "question": "Customer needs assistance - live test",
        "visitor": {
            "user_id": "test-live-001",
            "name": "Kanika Jain",
            "email": "kanika.jain@ramfincorp.com",
            "phone": "9876543210",
        },
    },
    headers=hdrs(org_token),
)
conv_data = resp.json().get("data", {})
conv_id = conv_data.get("conversation_id") or conv_data.get("id")
if not conv_id:
    print(f"  ERROR: {resp.json()}")
    sys.exit(1)
print(f"  Conversation created: {conv_id}")

# Step 3: Send visitor message
print("\n[3] Sending customer message...")
requests.post(
    f"{SALESIQ_BASE}/api/visitor/v1/{SCREEN_NAME}/conversations/{conv_id}/messages",
    json={"text": "Hi, I need help with my loan account. Please reply with any number to test."},
    headers=hdrs(org_token),
)
print("  Message sent: 'Hi, I need help with my loan account. Please reply with any number to test.'")

# Step 4: Poll for agent reply
print("\n[4] Waiting for agent reply on SalesIQ dashboard...")
print("    >>> Go to SalesIQ and reply with a NUMBER (e.g., 5) <<<\n")

last_seen_count = 0
while True:
    resp = requests.get(
        f"{SALESIQ_BASE}/api/v2/{SCREEN_NAME}/conversations/{conv_id}/messages",
        headers={"Authorization": f"Zoho-oauthtoken {v2_token}"},
    )
    messages = resp.json().get("data", [])

    # Look for new operator text messages
    operator_msgs = [
        m for m in messages
        if m.get("sender", {}).get("type") == "operator"
        and m.get("type") == "text"
    ]

    if len(operator_msgs) > last_seen_count:
        new_msg = operator_msgs[-1]
        agent_text = new_msg.get("message", {}).get("text", "")
        agent_name = new_msg.get("sender", {}).get("name", "Agent")
        print(f"  Agent ({agent_name}) replied: \"{agent_text}\"")

        # Step 5: Process the number
        try:
            number = float(agent_text.strip())
            result = int(number * 10)
            reply = f"You sent {int(number)}. Result: {int(number)} x 10 = {result}"
            print(f"\n[5] Processing: {int(number)} x 10 = {result}")
        except ValueError:
            reply = f"I received: \"{agent_text}\" — but that's not a number. Please send a number."
            print(f"\n[5] Not a number, asking again...")

        # Step 6: Send result back as visitor message
        print(f"\n[6] Sending reply back to SalesIQ...")
        requests.post(
            f"{SALESIQ_BASE}/api/visitor/v1/{SCREEN_NAME}/conversations/{conv_id}/messages",
            json={"text": reply},
            headers=hdrs(org_token),
        )
        print(f"  Sent: \"{reply}\"")

        last_seen_count = len(operator_msgs)

        print(f"\n    >>> Send another number, or type 'close' in SalesIQ to end <<<\n")

    # Check if conversation was closed
    close_msgs = [
        m for m in messages
        if m.get("type") == "info"
        and m.get("message", {}).get("mode") == "chatclosed"
    ]
    if close_msgs:
        print("\n[7] Conversation closed by agent.")
        break

    time.sleep(3)

print("\n" + "=" * 60)
print("   TEST COMPLETE")
print("=" * 60 + "\n")
