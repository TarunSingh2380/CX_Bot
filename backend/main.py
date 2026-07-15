"""
Ram Fincorp — Customer Support Chatbot Backend

Endpoints:
  POST /identify  — look up customer by email/phone, pre-fetch all context
  POST /chat      — handle chat with tiered LLM logic
"""

import json
import os
from pathlib import Path
from typing import Dict, List, Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from openai import OpenAI
from pydantic import BaseModel

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

import customer_api  # noqa: E402
import enrichment  # noqa: E402

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL = "gpt-5-mini"

client = OpenAI(api_key=OPENAI_API_KEY)

app = FastAPI(title="Ram Fincorp Support Chatbot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------
class IdentifyRequest(BaseModel):
    email: Optional[str] = None
    phone: Optional[str] = None


class IdentifyResponse(BaseModel):
    found: bool
    customerID: Optional[str] = None
    leadID: Optional[str] = None
    email: Optional[str] = None
    mobile: Optional[str] = None


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    message: str
    conversation_history: List[Message] = []
    email: Optional[str] = None
    phone: Optional[str] = None


VALID_ACTIONS = {
    "GREETING", "RESOLVE", "ESCALATE", "CLARIFY",
    "EXISTING_TICKET", "SEND_NOC",
}


class ChatResponse(BaseModel):
    reply: str
    action: str
    category: Optional[str] = None
    confidence: int
    ticket_number: Optional[str] = None
    options: Optional[List[str]] = None


# ---------------------------------------------------------------------------
# Predefined quick-action options
# ---------------------------------------------------------------------------
QUICK_OPTIONS = [
    "Check Loan Details",
    "View Payment History",
    "Request NOC",
    "Other Query",
]


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """\
You are a helpful customer support assistant for Ram Fincorp, a loan and \
personal finance company based in India. You are a CX agent in a highly \
regulated industry where the regulator looks down terribly at customer \
harassment issues. So, keep that in mind while interacting with any customer. \
You have to be assertive but polite always.

You help customers with queries related to their loans, EMI payments, loan \
status, NOC requests, repayment schedules, late fees, and general account \
questions. You can handle English and Hinglish (Hindi + English mixed) messages.

For every customer message, you must respond with a JSON object in this exact format:
{
  "action": one of "GREETING" | "RESOLVE" | "EXISTING_TICKET" | "CLARIFY" | "ESCALATE" | "SEND_NOC",
  "reply": "your message to the customer",
  "category": "escalation category or null",
  "confidence": 0-100,
  "ticket_number": "matching ticket number or null"
}

Follow these decision tiers IN ORDER:

TIER 0 — GREETING:
  If the user sends a greeting (hi, hello, hey, namaste, haan, etc.), respond \
with a friendly welcome and ask how you can help. Set action to "GREETING". \
The system will automatically show quick-action buttons — do NOT list the \
options in your reply text.

TIER 1 — QUICK OPTIONS (Loan Details / Payment History / NOC):
  If the user asks about their loan details, payment history, or NOC:
  - For LOAN DETAILS: summarize the key loan information from the \
"LOAN DETAILS" section of the customer context (loan amount, disbursed amount, \
outstanding, status, EMIs paid, repayment date, etc.). Set action to "RESOLVE".
  - For PAYMENT HISTORY: summarize the recent transactions from the \
"RECENT PAYMENTS" section. Show dates, amounts, types clearly. Set action to "RESOLVE".
  - For NOC REQUEST: check the "NOC ELIGIBILITY" section.
    - If ELIGIBLE: tell the customer they are eligible and ask "Shall I send \
the No Dues Certificate to your registered email?" Set action to "RESOLVE".
    - If NOT ELIGIBLE: explain they are not currently eligible for NOC. \
Set action to "RESOLVE".
  - For "Other Query": ask the customer to describe their query. Set action to "RESOLVE".

TIER 1.5 — SEND NOC:
  If the customer confirms they want the NOC sent (after you told them they \
are eligible), set action to "SEND_NOC". The system will trigger the NOC \
delivery. Your reply should say something like "Sending your No Dues Certificate now..."

TIER 2 — EXISTING TICKET:
  For free-text queries, check if the customer's query is similar to any of \
their OPEN support tickets in the customer context. Compare against ticket \
subject, description, and thread content. If there is a match:
  - Set action to "EXISTING_TICKET"
  - In your reply, tell them you found an existing ticket, mention the ticket \
number, summarize the status and last agent response if any.
  - Set ticket_number to the matching ticket number.

TIER 3 — RESOLVE (high confidence only):
  If no existing ticket matches AND you can answer from the customer context \
or general finance knowledge WITH confidence > 90, resolve directly.
  - Set action to "RESOLVE"

TIER 4 — CLARIFY:
  If the query is too vague or unclear, ask ONE specific follow-up question.
  - Set action to "CLARIFY"

TIER 5 — ESCALATE:
  If you cannot resolve with high confidence, categorize the query and \
escalate. Pick the most relevant category:
  1. Payment Dispute
  2. Waiver / Penalty Request
  3. Loan Closure / NOC
  4. Disbursement Issue
  5. Legal / Harassment Complaint
  6. Account / App Issue
  7. Other / Complex Query
  Set action to "ESCALATE", category to the chosen one.

Additional rules:
- Always respond in the same language the customer used (English or Hinglish).
- Keep replies concise and friendly.
- Never make up specific account details, balances, or dates — only use data \
from the customer context provided below.
- When showing financial data, format amounts with Rs and commas.
- Confidence reflects how sure you are (90+ = very sure, below 70 = uncertain).

Respond ONLY with the JSON object, no extra text.\
"""

VALID_CATEGORIES = {
    "Payment Dispute",
    "Waiver / Penalty Request",
    "Loan Closure / NOC",
    "Disbursement Issue",
    "Legal / Harassment Complaint",
    "Account / App Issue",
    "Other / Complex Query",
}


def _fallback_response() -> ChatResponse:
    return ChatResponse(
        action="CLARIFY",
        reply=(
            "Sorry, I didn't quite catch that. Could you please rephrase or "
            "give me a bit more detail about your query?"
        ),
        category=None,
        confidence=50,
        ticket_number=None,
        options=None,
    )


def _parse_llm_json(raw: str) -> ChatResponse:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found")
    data = json.loads(text[start : end + 1])

    action = str(data.get("action", "")).upper()
    if action not in VALID_ACTIONS:
        raise ValueError(f"Invalid action: {action!r}")

    reply = str(data.get("reply", "")).strip()
    if not reply:
        raise ValueError("Empty reply")

    category = data.get("category")
    if category in (None, "", "null", "None"):
        category = None
    else:
        category = str(category).strip()
        if action != "ESCALATE" or category not in VALID_CATEGORIES:
            category = category if category in VALID_CATEGORIES else None

    try:
        confidence = int(round(float(data.get("confidence", 50))))
    except (TypeError, ValueError):
        confidence = 50
    confidence = max(0, min(100, confidence))

    ticket_number = data.get("ticket_number")
    if ticket_number in (None, "", "null", "None"):
        ticket_number = None
    else:
        ticket_number = str(ticket_number).strip()
    if action != "EXISTING_TICKET":
        ticket_number = None

    options = None
    if action == "GREETING":
        options = QUICK_OPTIONS

    return ChatResponse(
        action=action,
        reply=reply,
        category=category,
        confidence=confidence,
        ticket_number=ticket_number,
        options=options,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"status": "ok", "service": "Ram Fincorp Support Chatbot"}


@app.post("/identify", response_model=IdentifyResponse)
def identify(req: IdentifyRequest) -> IdentifyResponse:
    """Customer enters email/phone → we identify and pre-warm the context cache."""
    context, cust = enrichment.build_customer_context(
        email=req.email, phone=req.phone
    )
    if not cust.get("customerID"):
        return IdentifyResponse(found=False)
    return IdentifyResponse(
        found=True,
        customerID=cust["customerID"],
        leadID=cust["leadID"],
        email=cust["email"],
        mobile=cust["mobile"],
    )


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    context, cust = enrichment.build_customer_context(
        email=req.email, phone=req.phone
    )
    system_content = SYSTEM_PROMPT
    if context:
        system_content = f"{SYSTEM_PROMPT}\n\n{context}"

    messages = [{"role": "system", "content": system_content}]
    for m in req.conversation_history:
        messages.append({"role": m.role, "content": m.content})
    messages.append({"role": "user", "content": req.message})

    try:
        completion = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            response_format={"type": "json_object"},
        )
        raw = completion.choices[0].message.content or ""
        response = _parse_llm_json(raw)

        # Intercept SEND_NOC: call the actual API and replace the reply.
        if response.action == "SEND_NOC" and cust.get("leadID"):
            noc_result = customer_api.send_noc(cust["leadID"])
            if noc_result["success"]:
                response.reply = (
                    "Your No Dues Certificate (NOC) has been sent successfully "
                    "to your registered email address. Please check your inbox."
                )
            else:
                response.reply = (
                    "Sorry, we couldn't send the NOC right now. "
                    f"Reason: {noc_result['message']}. "
                    "Please try again later or contact our support team."
                )
                response.action = "RESOLVE"

        return response
    except Exception as exc:
        print(f"[chat] error: {exc}")
        return _fallback_response()


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if FRONTEND_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIR / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def serve_frontend(full_path: str):
        file = FRONTEND_DIR / full_path
        if file.is_file():
            return FileResponse(file)
        return FileResponse(FRONTEND_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
