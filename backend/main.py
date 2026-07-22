"""
Ram Fincorp — Customer Support Chatbot Backend

Endpoints:
  POST /identify    — look up customer by email/phone, pre-fetch all context
  GET  /categories  — predefined categories with common questions
  POST /chat        — handle chat with tiered LLM logic + ticket creation
"""

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("chatbot")
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from openai import OpenAI
from pydantic import BaseModel

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"))

import customer_api  # noqa: E402
import enrichment  # noqa: E402
import salesiq  # noqa: E402  — kept for future use, disconnected from flow
import zoho  # noqa: E402

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
# Categories & common questions
# ---------------------------------------------------------------------------
CATEGORIES = [
    {
        "id": "loan_status",
        "name": "Loan Status",
        "questions": [
            "What is the status of my loan application?",
            "Has my loan been approved?",
            "Why is my loan still under review?",
            "When will my loan be disbursed?",
            "My application shows Disbursed but I have not received the money.",
            "Why was my loan application rejected?",
            "Can I reapply after rejection?",
        ],
    },
    {
        "id": "emi_repayment",
        "name": "EMI & Repayment",
        "questions": [
            "What is my next EMI due date?",
            "How much EMI do I need to pay?",
            "What is my outstanding loan amount?",
            "How can I make a payment?",
            "My payment is not reflecting.",
            "I paid my EMI but received a bounce charge.",
            "Why has a penalty been charged?",
            "Can I pay my EMI before the due date?",
            "Can I close my loan early?",
        ],
    },
    {
        "id": "nach_autodebit",
        "name": "NACH / Auto-Debit",
        "questions": [
            "What is NACH?",
            "Why did my auto-debit fail?",
            "How can I update my bank account?",
            "How can I cancel auto-debit?",
        ],
    },
    {
        "id": "loan_closure_noc",
        "name": "Loan Closure & NOC",
        "questions": [
            "Is my loan closed?",
            "How can I download my NOC?",
            "I have paid my loan but it still shows active.",
            "When will my NOC be generated?",
            "How long does it take to update loan closure?",
        ],
    },
    {
        "id": "refunds",
        "name": "Refunds",
        "questions": [
            "When will I receive my refund?",
            "Why was money deducted twice?",
            "How can I request a refund?",
            "What is the refund timeline?",
        ],
    },
    {
        "id": "cooling_off",
        "name": "Cooling-Off Period",
        "questions": [
            "What is the cooling-off period?",
            "Can I cancel my loan during the cooling-off period?",
            "I repaid within the cooling-off period. Why is my loan still active?",
        ],
    },
    {
        "id": "cibil",
        "name": "Credit Bureau / CIBIL",
        "questions": [
            "When will my CIBIL be updated?",
            "Why is my loan showing active in CIBIL?",
            "How can I raise a CIBIL correction request?",
            "My CIBIL score has decreased. Why?",
        ],
    },
    {
        "id": "reloan",
        "name": "Re-Loan / Eligibility",
        "questions": [
            "Am I eligible for another loan?",
            "Why am I not able to apply for a re-loan?",
            "How much loan can I get?",
            "How is my loan eligibility calculated?",
        ],
    },
    {
        "id": "profile",
        "name": "Customer Profile",
        "questions": [
            "How can I update my mobile number?",
            "How can I update my email ID?",
            "How can I update my bank account details?",
            "How can I update my PAN card details?",
        ],
    },
    {
        "id": "technical",
        "name": "Technical Issues",
        "questions": [
            "OTP not received.",
            "App is not opening.",
            "Login issue.",
            "Payment link not working.",
            "Unable to upload documents.",
            "Sanction letter not downloading.",
        ],
    },
    {
        "id": "payments",
        "name": "Payments & Transactions",
        "questions": [
            "Show my recent transactions.",
            "What is my disbursed amount?",
        ],
    },
    {
        "id": "calculations",
        "name": "EMI & Interest Calculator",
        "questions": [
            "Calculate my EMI.",
            "What is my interest rate?",
        ],
    },
    {
        "id": "other",
        "name": "Other",
        "questions": [],
    },
]

CATEGORY_NAMES = [c["name"] for c in CATEGORIES]

GREETING_CATEGORIES = [
    "Loan Status",
    "EMI & Repayment",
    "Loan Closure & NOC",
    "Payments & Transactions",
    "EMI & Interest Calculator",
    "Other",
]


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
    category: Optional[str] = None


VALID_ACTIONS = {
    "GREETING", "RESOLVE", "ESCALATE", "CLARIFY",
    "EXISTING_TICKET", "SEND_NOC",
}
# EXISTING_TICKET is kept in VALID_ACTIONS for backwards compat but is now
# handled in code (not by the LLM) — see _check_existing_ticket() below.


class ChatResponse(BaseModel):
    reply: str
    action: str
    category: Optional[str] = None
    confidence: int
    ticket_number: Optional[str] = None
    options: Optional[List[str]] = None


# ---------------------------------------------------------------------------
# SalesIQ agent sessions (kept but disconnected from main flow)
# ---------------------------------------------------------------------------
_agent_sessions: Dict[str, str] = {}


def _customer_key(email: Optional[str], phone: Optional[str]) -> str:
    return f"{(email or '').lower()}|{phone or ''}"


class AgentPollRequest(BaseModel):
    conversation_id: str
    last_seen: int = 0


class AgentPollResponse(BaseModel):
    messages: List[Dict]
    closed: bool


class AgentSendRequest(BaseModel):
    conversation_id: str
    message: str


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
  "action": one of "GREETING" | "RESOLVE" | "CLARIFY" | "ESCALATE" | "SEND_NOC",
  "reply": "your message to the customer",
  "category": "query category or null",
  "confidence": 0-100,
  "ticket_number": "matching ticket number or null",
  "ticket_subject": "short subject for support ticket (only when ESCALATE)",
  "ticket_description": "detailed description for support ticket (only when ESCALATE)"
}

Follow these decision tiers IN ORDER:

TIER 0 — GREETING:
  If the user sends a greeting (hi, hello, hey, namaste, haan, etc.), respond \
with a friendly welcome and ask how you can help. Set action to "GREETING". \
The system will automatically show category buttons — do NOT list the \
categories in your reply text.

TIER 1 — SEND_NOC:
  If the customer confirms they want the NOC sent (after you told them they \
are eligible), set action to "SEND_NOC". The system will trigger the NOC \
delivery. Your reply should say something like "Sending your No Dues Certificate now..."

TIER 2 — RESOLVE (USE THIS whenever customer data is available):
  If the customer context below contains data that can answer the query, \
you MUST resolve directly. Do NOT escalate when you have the data.
  - Set action to "RESOLVE"
  - For LOAN STATUS queries (application status, approved, disbursed, rejected, \
under review): look at LOAN DETAILS — the "Status" field is the answer. \
If you see a Status like "Active", "Closed", "Disbursed", etc., RESOLVE it.
  - For EMI & REPAYMENT queries (next EMI, outstanding, paid amount): look at \
LOAN DETAILS for amounts/dates and RECENT PAYMENTS for transaction history.
  - For LOAN CLOSURE & NOC queries: use NOC ELIGIBILITY + loan Status.
  - For PAYMENTS queries: use RECENT PAYMENTS data.
  - For EMI CALCULATOR queries: use the loan amount, tenure, and interest rate \
from LOAN DETAILS to calculate.
  - ONLY use data from the customer context. Never invent details.
  - Set confidence to 95+ when the data clearly answers the question.

TIER 3 — CLARIFY:
  If the query is too vague or unclear, ask ONE specific follow-up question \
to clearly understand what the customer needs. Do NOT escalate vague queries — \
always clarify first.
  - Set action to "CLARIFY"

TIER 4 — ESCALATE (create support ticket):
  If you cannot resolve with confidence >= 95, OR you don't have sufficient \
data to answer the query accurately, escalate by creating a support ticket:
  - Set action to "ESCALATE"
  - Set category to the most relevant category
  - Set ticket_subject: Write a short, clear, human-readable subject (max 80 chars). \
Write it as a support agent would — e.g. "Customer asking about next EMI due date" \
or "NACH cancellation request". Do NOT include loan numbers, customer IDs, or \
technical codes in the subject. No prefixes like "[TEST]".
  - Set ticket_description: Write a clean, well-structured ticket body that a \
support agent can read and act on quickly. Format it like this:
    1. **Customer Query**: What the customer is asking in plain language (1-2 sentences).
    2. **Customer Details**: Name/ID, email, phone — one line each.
    3. **Relevant Account Info**: Only the key details relevant to this specific \
query — don't dump all data. Use bullet points with labels.
    4. **What's Missing**: What data or action is needed to resolve this.
    5. **Suggested Action**: What the support agent should do.
    Use line breaks between sections. Write professionally but concisely. \
Do NOT dump raw API data or internal field names.
  - Your reply should tell the customer that a support ticket is being created \
for their query and our team will look into it.

Additional rules:
- Always respond in the same language the customer used (English or Hinglish).
- Keep replies concise and friendly.
- Never make up specific account details, balances, or dates — only use data \
from the customer context provided below.
- When showing financial data, format amounts with Rs and commas.
- CRITICAL: If the LOAN DETAILS section shows data (Status, Loan Amount, etc.), \
you MUST use it to answer loan-related queries — do NOT escalate.
- Only ESCALATE when the customer context genuinely does not contain the data \
needed to answer, or when the customer needs a human action (update profile, \
raise dispute, etc.).
- Do not guess or provide generic answers when specific data is needed — but \
if the data IS in the context, use it confidently.

Respond ONLY with the JSON object, no extra text.\
"""


def _check_existing_ticket(
    new_subject: str, open_tickets: List[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """Check if an open ticket already covers this issue (subject-based match).
    Returns the matching ticket dict or None."""
    if not open_tickets or not new_subject:
        return None
    new_words = set(new_subject.lower().split())
    for tk in open_tickets:
        tk_subject = (tk.get("subject") or "").lower()
        tk_words = set(tk_subject.split())
        common = new_words & tk_words
        noise = {"for", "the", "a", "an", "of", "and", "is", "in", "to",
                 "my", "on", "with", "not", "loan", "customer", "request",
                 "asking", "about", "need", "status", "query", "check"}
        meaningful = common - noise
        if len(meaningful) >= 3:
            log.info("[ticket-match] Matched existing ticket #%s — common words: %s",
                     tk.get("number"), meaningful)
            return tk
    return None


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


def _parse_llm_json(raw: str) -> tuple:
    """Parse LLM JSON response. Returns (ChatResponse, extra_dict)."""
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
        options = GREETING_CATEGORIES

    response = ChatResponse(
        action=action,
        reply=reply,
        category=category,
        confidence=confidence,
        ticket_number=ticket_number,
        options=options,
    )

    extra = {
        "ticket_subject": str(data.get("ticket_subject", "")).strip(),
        "ticket_description": str(data.get("ticket_description", "")).strip(),
    }

    return response, extra


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"status": "ok", "service": "Ram Fincorp Support Chatbot"}


@app.get("/categories")
def get_categories():
    log.info("[categories] Serving %d categories", len(CATEGORIES))
    return CATEGORIES


@app.post("/identify", response_model=IdentifyResponse)
def identify(req: IdentifyRequest) -> IdentifyResponse:
    log.info("[identify] email=%s phone=%s", req.email, req.phone)
    context, cust = enrichment.build_customer_context(
        email=req.email, phone=req.phone
    )
    if not cust.get("customerID"):
        log.info("[identify] Customer NOT FOUND")
        return IdentifyResponse(found=False)
    log.info("[identify] Customer FOUND — id=%s lead=%s", cust["customerID"], cust["leadID"])
    return IdentifyResponse(
        found=True,
        customerID=cust["customerID"],
        leadID=cust["leadID"],
        email=cust["email"],
        mobile=cust["mobile"],
    )


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    log.info("[chat] message=%s | email=%s | category=%s", req.message[:60], req.email, req.category)

    # Check if user selected a category name — return its questions, no LLM needed
    category_match = next(
        (c for c in CATEGORIES if c["name"].lower() == req.message.strip().lower()),
        None,
    )
    if category_match:
        if category_match["id"] == "other":
            log.info("[chat] 'Other' selected — asking user to type query")
            return ChatResponse(
                action="RESOLVE",
                reply="Sure! Please type your question or describe your issue, and I'll do my best to help. If I'm unable to resolve it, I'll create a support ticket for you.",
                category="Other",
                confidence=100,
                ticket_number=None,
                options=None,
            )
        log.info("[chat] Category selected: %s — returning %d questions",
                 category_match["name"], len(category_match["questions"]))
        return ChatResponse(
            action="RESOLVE",
            reply=f"Here are common questions for {category_match['name']}. You can select one or type your own query:",
            category=category_match["name"],
            confidence=100,
            ticket_number=None,
            options=category_match["questions"],
        )

    context, cust = enrichment.build_customer_context(
        email=req.email, phone=req.phone
    )
    log.info("[chat] Context length=%d chars | has_context=%s", len(context), bool(context))
    if context:
        for marker in ["LOAN DETAILS", "RECENT PAYMENTS", "NOC ELIGIBILITY"]:
            if f"(no " in context.split(marker)[-1][:60] if marker in context else "":
                log.info("[chat] Context section '%s' is EMPTY", marker)
            elif marker in context:
                log.info("[chat] Context section '%s' has data", marker)

    system_content = SYSTEM_PROMPT
    if req.category:
        system_content += f"\n\nThe customer selected the category: {req.category}"
    if context:
        system_content = f"{system_content}\n\n{context}"

    messages = [{"role": "system", "content": system_content}]
    for m in req.conversation_history:
        messages.append({"role": m.role, "content": m.content})
    messages.append({"role": "user", "content": req.message})

    try:
        log.info("[chat] Calling LLM (%s)...", MODEL)
        completion = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            response_format={"type": "json_object"},
        )
        raw = completion.choices[0].message.content or ""
        log.info("[chat] Raw LLM output: %s", raw[:500])
        response, extra = _parse_llm_json(raw)
        log.info("[chat] LLM response — action=%s confidence=%s category=%s",
                 response.action, response.confidence, response.category)

        if response.action == "RESOLVE":
            log.info("[chat] RESOLVE — answering with confidence=%s", response.confidence)

        if response.action == "CLARIFY":
            log.info("[chat] CLARIFY — asking follow-up question")

        if response.action == "EXISTING_TICKET":
            log.info("[chat] LLM returned EXISTING_TICKET — converting to ESCALATE for code-based matching")
            response.action = "ESCALATE"

        if response.action == "GREETING":
            log.info("[chat] GREETING — showing %d answerable categories", len(GREETING_CATEGORIES))

        # Intercept SEND_NOC: call the actual API and replace the reply.
        if response.action == "SEND_NOC" and cust.get("leadID"):
            log.info("[chat] SEND_NOC — calling NOC API for lead=%s", cust["leadID"])
            noc_result = customer_api.send_noc(cust["leadID"])
            if noc_result["success"]:
                log.info("[chat] NOC sent successfully")
                response.reply = (
                    "Your No Dues Certificate (NOC) has been sent successfully "
                    "to your registered email address. Please check your inbox."
                )
            else:
                log.error("[chat] NOC failed — %s", noc_result["message"])
                response.reply = (
                    "Sorry, we couldn't send the NOC right now. "
                    f"Reason: {noc_result['message']}. "
                    "Please try again later or contact our support team."
                )
                response.action = "RESOLVE"

        # Intercept ESCALATE: check for existing ticket first, then create.
        if response.action == "ESCALATE":
            email = cust.get("email") or req.email or ""
            category = response.category or req.category or "General Query"

            ticket_subject = extra.get("ticket_subject") or f"{category} — {req.message[:80]}"
            ticket_description = extra.get("ticket_description") or req.message

            log.info("[chat] ESCALATE — category=%s", category)
            log.info("[chat] ESCALATE — ticket_subject=%s", ticket_subject[:100])

            open_tickets = zoho.get_open_tickets_with_context(email) if email else []
            existing = _check_existing_ticket(ticket_subject, open_tickets)

            if existing:
                tk_num = existing.get("number") or existing.get("ticket_id")
                log.info("[chat] ESCALATE → EXISTING_TICKET #%s", tk_num)
                response.action = "EXISTING_TICKET"
                response.ticket_number = str(tk_num)
                response.reply = (
                    f"I found an existing open ticket #{tk_num} for this issue. "
                    f"Our team is already working on it and will update you soon."
                )
            else:
                log.info("[chat] ESCALATE — no existing ticket match, creating new")
                log.info("[chat] ESCALATE — ticket_description=%s", ticket_description[:150])
                contact_id = zoho.find_or_create_contact(email) if email else None
                log.info("[chat] ESCALATE — contact_id=%s for email=%s", contact_id, email)
                if contact_id:
                    ticket = zoho.create_ticket(
                        contact_id=contact_id,
                        email=email,
                        subject=ticket_subject,
                        description=ticket_description,
                        category=category,
                    )
                    if ticket:
                        response.ticket_number = ticket["number"]
                        response.reply = (
                            f"I've created a support ticket for your query. "
                            f"Your ticket number is #{ticket['number']}. "
                            f"Our team will review it and get back to you shortly."
                        )
                        log.info("[chat] ESCALATE SUCCESS — ticket #%s", ticket["number"])
                    else:
                        log.error("[chat] ESCALATE FAILED — ticket creation failed")
                        response.reply = (
                            "I'm sorry, I couldn't create a support ticket right now. "
                            "Please try again or contact our support team directly."
                        )
                else:
                    log.error("[chat] ESCALATE FAILED — could not find/create contact for %s", email)
                    response.reply = (
                        "I'm sorry, I couldn't create a support ticket right now. "
                        "Please try again or contact our support team directly."
                    )

        return response
    except Exception as exc:
        log.error("[chat] Error: %s", exc, exc_info=True)
        return _fallback_response()


# ---------------------------------------------------------------------------
# SalesIQ agent endpoints (kept for future use, disconnected from ESCALATE)
# ---------------------------------------------------------------------------
@app.post("/agent/poll", response_model=AgentPollResponse)
def agent_poll(req: AgentPollRequest) -> AgentPollResponse:
    log.debug("[agent/poll] conv=%s last_seen=%s", req.conversation_id, req.last_seen)
    all_msgs = salesiq.get_messages(req.conversation_id)

    agent_messages = []
    closed = False

    for m in all_msgs:
        seq = int(m.get("sequence_id", 0))
        if seq <= req.last_seen:
            continue

        if m.get("type") == "info":
            mode = m.get("message", {}).get("mode", "")
            if mode == "chatclosed":
                closed = True
                log.info("[agent/poll] Chat CLOSED by agent | conv=%s", req.conversation_id)
            continue

        sender_type = m.get("sender", {}).get("type", "")
        if sender_type == "operator":
            text = m.get("message", {}).get("text", "")
            sender_name = m.get("sender", {}).get("name", "Agent")
            log.info("[agent/poll] New agent message | from=%s | msg=%s", sender_name, text[:80])
            agent_messages.append({
                "sequence_id": seq,
                "text": text,
                "sender": sender_name,
                "time": m.get("time", ""),
            })

    return AgentPollResponse(messages=agent_messages, closed=closed)


@app.post("/agent/send")
def agent_send(req: AgentSendRequest):
    log.info("[agent/send] conv=%s | msg=%s", req.conversation_id, req.message[:80])
    success = salesiq.send_visitor_message(req.conversation_id, req.message)
    log.info("[agent/send] Result: %s", "SUCCESS" if success else "FAILED")
    return {"success": success}


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
