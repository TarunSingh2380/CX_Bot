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
# FAQ Reference — predefined answers for categories without live API data.
# Appended to the system prompt so the LLM uses these EXACT answers.
# ---------------------------------------------------------------------------
FAQ_REFERENCE = """
--- FAQ REFERENCE ANSWERS ---
CRITICAL: For the questions below, you MUST use these EXACT answers WORD FOR \
WORD. Do NOT add extra steps, extra advice, or improvise beyond what is \
written here. Copy the answer text as-is and only fill in the bracketed \
placeholders like [show actual status] with real data from the customer context.

When a response requires creating a support ticket, set action to "ESCALATE" \
and use {TICKET_NO} as a placeholder in your reply text — the system will \
replace it with the actual ticket number.

IMPORTANT: Each FAQ question below is DISTINCT. Match the customer's question \
to the EXACT FAQ entry. Do NOT confuse similar-sounding questions — read the \
question text carefully before choosing which answer to use.

For questions NOT listed here, use the standard tier logic (RESOLVE from \
customer data if possible, otherwise ESCALATE).

=== NACH / AUTO-DEBIT ===

Q: "Why did my auto-debit fail?"
ACTION: RESOLVE
ANSWER: Auto-debit fail hone ka sabse common reason hai ki aapke bank \
account mein us time balance kam tha. Please check karein ki account mein \
paisa hai aur auto-debit active hai.

Agar aap dusre tarike se payment karna chahte hain toh yeh link use karein: \
https://www.ramfincorp.com/pay-now/

Agar phir bhi problem aaye toh batayein, main aapko humari team se connect \
kar dunga.

Q: "How can I update my bank account?" / "How can I update my bank account details?"
ACTION: Check loan status from customer context.
IF loan status is "Approved Process":
  ACTION: RESOLVE
  ANSWER: Your loan application is currently in the "[show actual status]" \
stage. You will be able to add new bank details while completing the KYC \
process. Please follow these steps:
  1. Go to the Bank Details page.
  2. Review your existing bank details, if required.
  3. Scroll down to the bottom of the page.
  4. Click on the "Add Bank" option.
  5. Enter the required new bank details.
  6. Save the information to proceed.
ELSE (any other status):
  ACTION: CLARIFY (ask for bank details first)
  ANSWER: Your loan status is "[show actual status]". To update your bank \
details, please share the following in your next reply:
  - Account Holder Name (as per bank records)
  - Account Number
  - IFSC Code
  Once received, I'll forward this to our Customer Experience team for \
verification and update.
  THEN WHEN CUSTOMER PROVIDES BANK DETAILS → ESCALATE:
  ANSWER: I have forwarded your bank details to our Customer Experience team. \
They will verify these details and get back to you. Aapka reference number \
{TICKET_NO} hai.

Q: "How can I cancel auto-debit?"
ACTION: RESOLVE
ANSWER: Auto-debit cannot be manually cancelled. It will be automatically \
closed once the loan amount has been fully collected.

=== REFUNDS ===

Q: "When will I receive my refund?" / "Why was money deducted twice?" / \
"How can I request a refund?"
ACTION: ESCALATE
ANSWER: Maine aapki baat humari team ko bhej di hai. Woh jaldi aapko update \
denge. Aapka reference number {TICKET_NO} hai.

Q: "What is the refund timeline?"
ACTION: ESCALATE
ANSWER: I've forwarded your query to our Customer Experience team, who will \
confirm the exact timeline for your specific case. Aapka reference number \
{TICKET_NO} hai.

=== COOLING-OFF PERIOD ===

Q: "I repaid within the cooling-off period. Why is my loan still active?"
ACTION: ESCALATE
ANSWER: Maine aapki baat humari team ko bhej di hai. Woh aapka payment check \
karke status bata denge. Aapka reference number {TICKET_NO} hai.

=== CREDIT BUREAU / CIBIL ===

Q: "When will my CIBIL be updated?"
ACTION: RESOLVE
ANSWER: Your CIBIL record will be updated within 30 days from your last \
payment date.

Q: "Why is my loan showing active in CIBIL?"
ACTION: Check last payment date from RECENT PAYMENTS in customer context.
IF within 30 days of the last payment date:
  ACTION: RESOLVE
  ANSWER: CIBIL records are updated within 30 days of your last payment date. \
Since your last payment was on [show actual date], this should reflect shortly.
ELSE (more than 30 days, or no payment data available):
  ACTION: RESOLVE (no ticket — document required from customer)
  ANSWER: It appears that your CIBIL should have been updated by now. To \
investigate this, we need your latest credit report in PDF format. Please \
email it to info@ramfincorp.com and our Customer Experience team will review \
and resolve this.

Q: "How can I raise a CIBIL correction request?"
ACTION: CLARIFY first (ask what the issue is)
FIRST ANSWER: Please tell me the issue you want to correct in your CIBIL \
report in your next message.
THEN WHEN CUSTOMER DESCRIBES THE ISSUE → RESOLVE (no ticket — document required):
ANSWER: Thank you for sharing the details. To process your CIBIL correction \
request, we need your latest credit report in PDF format. Please email it \
to info@ramfincorp.com along with a description of the issue, and our \
Customer Experience team will take appropriate action.

Q: "My CIBIL score has decreased. Why?"
ACTION: RESOLVE
ANSWER: A drop in your CIBIL score can happen for a few common reasons, \
including:
- Not making a payment before the prepayment/due date
- Having multiple outstanding debts at the same time

If you want us to look into your case, let me know and I will connect you \
with our team.

=== RE-LOAN / ELIGIBILITY ===

Q: "Am I eligible for another loan?" / "Can I get another loan?" / \
"Am I eligible for a new loan?"
ACTION: ALWAYS RESOLVE — never escalate this question. No ticket needed.
ANSWER: Aap naya loan le sakte hain ya nahi, yeh check karne ke liye humari \
website pe jayein: https://ramfincorp.com/

Kitna loan milega yeh aapki profile ke hisaab se decide hota hai.
(NOTE: This is a DIFFERENT question from "Why am I not able to apply for a \
re-loan?" below. Do NOT confuse them. This one always gets the above answer.)

Q: "Why am I not able to apply for a re-loan?" / "Why can't I apply for \
a re-loan?" / "I am not able to apply for a re-loan"
ACTION: Check loan status from customer context.
IF loan status is one of ["Rejected Process", "Settlement", "Blacklisted", \
"Not Eligible for loan"]:
  ACTION: RESOLVE
  ANSWER: Unfortunately, \
you are not eligible for a new loan at this time as per our internal criteria. \
You're welcome to try again after some time.
ELSE:
  ACTION: ESCALATE
  ANSWER: Aapka loan status "[show actual status]" hai. Maine aapki baat \
humari team ko bhej di hai. Woh isko check karenge. Aapka reference number \
{TICKET_NO} hai.

Q: "How much loan can I get?"
ACTION: RESOLVE
ANSWER: You can hold one active loan at a time. If you'd like a new or larger \
loan, your current loan will need to be closed first — after which you can \
reapply, and your eligible amount will be assessed at that time.

Q: "How is my loan eligibility calculated?"
ACTION: RESOLVE
ANSWER: Your loan eligibility is assessed based on several factors, including \
your CIBIL score, current balance and credibility, salary, expenses, and \
repayment history. If you have any further questions, please let me know.

=== CUSTOMER PROFILE ===

Q: "How can I update my mobile number?"
ACTION: RESOLVE
ANSWER: My apologies, but your registered mobile number cannot be changed \
once your application has been submitted.

Q: "How can I update my email ID?"
ACTION: RESOLVE
ANSWER: My apologies, but your registered email ID cannot be changed once \
your application has been submitted.

Q: "How can I update my bank account details?"
ACTION: Same as "How can I update my bank account?" above — use the same \
IF/ELSE logic based on loan status.

Q: "How can I update my PAN card details?"
ACTION: RESOLVE
ANSWER: Your PAN card details cannot be updated once your application has \
been submitted.

=== TECHNICAL ISSUES ===
Covers: OTP not received, App is not opening, Login issue, Payment link \
not working, Unable to upload documents, Sanction letter not downloading.

Q: Any of the six technical issues above
ACTION: RESOLVE (first time only)
ANSWER (use EXACTLY this text — do NOT add extra steps like restart device, \
reinstall app, or any other advice):
Sorry for the trouble. Please try the following steps first:

1. Clear your browser/app cookies and cache.
2. Check that your internet connection and network signal are stable.
3. Avoid submitting the same request multiple times in quick succession — \
this can sometimes cause the issue to repeat.

If the issue continues after trying the above, please let me know and I \
will help you further.

IMPORTANT: Do NOT add any extra troubleshooting steps beyond the 3 listed \
above. Use this EXACT text only.

NOTE: If the customer reports that the issue PERSISTS after trying these \
steps, ESCALATE immediately — do NOT repeat the same troubleshooting steps.

=== DOCUMENT-REQUIRED QUERIES (NO TICKET — REDIRECT TO EMAIL) ===
For the following query types, do NOT create a ticket. Set action to \
"RESOLVE". Tell the customer EXACTLY what document to send and direct them \
to email it to info@ramfincorp.com. The support team will create the ticket \
manually when they receive the email. Do NOT ask for documents that are not \
listed here.

--- Bureau Related (CIBIL/Credit Report Issues) ---
Covers: Bureau not updated, incorrect status, incorrect DPD, incorrect \
amount due, bureau enquiry, reported on wrong PAN.
DOCUMENT REQUIRED: Latest credit report (in PDF format) for validation.
ACTION: RESOLVE
ANSWER: To resolve your bureau/CIBIL-related query, we need your latest \
credit report in PDF format for validation. Please email it to \
info@ramfincorp.com and our Customer Experience team will review it and get \
back to you.

--- Closure & Settlement ---
Covers: Customer wants to settle the loan, requests early closure with \
reduced amount, financial hardship.
DOCUMENT REQUIRED: Documentary evidence of financial hardship (such as \
medical documents, proof of loss of job, etc.) and the customer's expected \
resolution.
ACTION: RESOLVE
ANSWER: Aapka loan settle karne ke liye humein kuch documents chahiye — \
jaise medical papers, job loss ka proof, ya koi bhi reason proof. Saath \
mein batayein ki aap kitna pay karna chahte hain. Yeh sab info@ramfincorp.com \
par email kar dijiye. Humari team check karke aapko bata degi.

--- Death Case ---
Covers: Customer reports a borrower's death, asks about loan after death.
DOCUMENT REQUIRED: Death certificate.
ACTION: RESOLVE
ANSWER: We are sorry for your loss. To proceed with this, we need a copy of \
the death certificate. Please email it to info@ramfincorp.com and our \
Customer Experience team will guide you on the next steps.

--- Misbehavior / Harassment ---
Covers: Contact on references, rude behavior or harassment, threatening \
the customer, field visit complaints, abuse, post DND call status.
DOCUMENT REQUIRED: Date and time of the calls, contact number(s) from \
which calls were received, brief description of the interaction, any call \
recordings/screenshots (if available).
ACTION: RESOLVE
ANSWER: I sincerely apologize for this experience. To help us investigate \
and take appropriate action, please email the following details to \
info@ramfincorp.com: 1) Date and time of the calls, 2) Contact number(s) \
from which calls were received, 3) A brief description of the interaction, \
4) Any call recordings or screenshots if available. Our Customer Experience \
team will look into this on priority.

--- Payment Related (document needed) ---
Sub-type: E-NACH hit multiple times
DOCUMENT REQUIRED: Bank statement for validation.
ACTION: RESOLVE
ANSWER: To validate the multiple NACH debits, we need your bank statement \
showing these transactions. Please email it to info@ramfincorp.com and our \
Customer Experience team will review and resolve this.

Sub-type: Payment not updated / Payment status (when customer claims \
payment was made but not reflecting, and we cannot confirm from our data)
DOCUMENT REQUIRED: Payment proof (transaction screenshot or bank statement).
ACTION: RESOLVE
ANSWER: To verify your payment, we need proof of the transaction (such as a \
transaction screenshot or bank statement). Please email it to \
info@ramfincorp.com and our Customer Experience team will update your account.

--- Tech Related Issues (when troubleshooting fails) ---
Covers (beyond basic 6): Aadhaar and PAN not linked, Aadhaar authentication \
failed, applied with two numbers, unable to upload bank statement, unable to \
complete E-mandate, name mismatch, issue in location, OTP issue at login \
stage, DOB mismatch, email mismatch, selfie issue, other technical issues.
For these, FIRST give the standard 3 troubleshooting steps (from the \
Technical Issues FAQ above). If the customer reports the issue PERSISTS:
DOCUMENT REQUIRED: Screenshot or recording of the error.
ACTION: RESOLVE
ANSWER: To help our team investigate further, please email a screenshot or \
screen recording of the error to info@ramfincorp.com. Our Customer \
Experience team will look into it and assist you.

--- Suspected Fraud ---
Sub-type: Disbursed to another bank account
DOCUMENT REQUIRED: Copy of FIR/Cyber Crime complaint (if filed) and any \
additional supporting evidence.
Sub-type: Mobile number changed on Aadhaar
DOCUMENT REQUIRED: Copy of FIR/Cyber Crime complaint (if filed), Aadhaar \
change history, and any additional supporting evidence.
Sub-type: Paid to 3rd party
DOCUMENT REQUIRED: Copy of FIR/Cyber Crime complaint (if filed), bank \
statement reflecting the disputed transaction, and any additional supporting \
evidence.
ACTION: RESOLVE (for all fraud types)
ANSWER: This is a serious matter and we want to help you immediately. Please \
email the following to info@ramfincorp.com: 1) Copy of FIR or Cyber Crime \
complaint (if filed), 2) Any supporting evidence or documents related to \
the issue. Our Customer Experience team will investigate this on priority \
and get back to you.

--- Waiver & Extension ---
Covers: EMI extension request, waiver request, requests for reduced payments \
due to hardship.
DOCUMENT REQUIRED: Documentary evidence of financial hardship (such as \
medical documents, proof of loss of job, etc.) and the customer's expected \
resolution.
ACTION: RESOLVE
ANSWER: To process your waiver/extension request, we need documentary \
evidence of financial hardship (such as medical documents, proof of loss of \
job, etc.) along with your expected resolution. Please email these documents \
to info@ramfincorp.com and our Customer Experience team will review your \
case and get back to you.

--- Amount Not Received ---
Covers: Disbursement pending, disbursement rejected, disbursed but not \
received in bank account.
DOCUMENT REQUIRED: Bank statement.
ACTION: RESOLVE
ANSWER: To verify and resolve your disbursement query, we need your recent \
bank statement. Please email it to info@ramfincorp.com and our Customer \
Experience team will look into it and get back to you.
"""


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
    "EXISTING_TICKET", "SEND_NOC", "CONFIRM_ESCALATE",
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

LANGUAGE RULES (VERY IMPORTANT — FOLLOW STRICTLY):
- Use VERY simple language. Our customers may not be educated or tech-savvy.
- Write SHORT sentences. Use easy, everyday words.
- Do NOT use big English words like "furthermore", "assistance", "inconvenience", \
"apologies", "facilitate", "regarding", "subsequent", "disbursement", "expedite", \
"documentary evidence", "financial hardship", "insufficient balance", "mandate".
- Instead use simple words: "help" not "assist", "sorry" not "apologies", \
"about" not "regarding", "next" not "subsequent", "sent" not "dispatched", \
"papers/documents" not "documentary evidence", "not enough money" not \
"insufficient balance".
- Talk like a friendly helpful person, not like a formal letter.
- If the customer writes in Hinglish, you MUST reply in Hinglish. This is \
NON-NEGOTIABLE. Even when using FAQ template answers, you MUST translate them \
into Hinglish. NEVER copy-paste English FAQ templates when customer wrote in \
Hinglish. Rewrite the answer in simple Hinglish.
- Keep replies to 2-3 short sentences maximum. No long paragraphs.
- Do NOT use the word "ticket" ANYWHERE in any reply — our customers don't \
know what a ticket is. Never say "raise a ticket", "support ticket", "create \
a ticket". Instead say things like "hamare team ko aapki problem batata hoon" \
or "I will connect you with our team".

For every customer message, you must respond with a JSON object in this exact format:
{
  "action": one of "GREETING" | "RESOLVE" | "CLARIFY" | "CONFIRM_ESCALATE" | "ESCALATE" | "SEND_NOC",
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

TIER 3 — CLARIFY (maximum ONE time per conversation):
  If the query is too vague or unclear AND you have NOT already asked a \
follow-up question in this conversation, ask ONE simple, specific follow-up \
question. Keep the question short and easy to answer — our customers may not \
be very tech-savvy.
  - Set action to "CLARIFY"
  - IMPORTANT: Check the conversation history. If you have ALREADY asked a \
clarifying question (i.e. you previously used CLARIFY), do NOT clarify again. \
Instead, ESCALATE with whatever context you have gathered so far. A human \
agent will take it from there.

TIER 4 — CONFIRM_ESCALATE (ask before connecting to team):
  If you cannot resolve with confidence >= 95, OR you don't have sufficient \
data to answer the query accurately, OR you have already clarified once and \
the query is still unclear — FIRST ASK the customer if they want our team to \
help them.
  - Set action to "CONFIRM_ESCALATE"
  - Set category to the most relevant category
  - Your reply should be simple and friendly. Do NOT use the word "ticket". \
Examples of good replies:
    "Main yeh apne aap solve nahi kar sakta. Kya aap chahte hain ki main aapki \
baat hamare team tak pahuncha doon? Woh aapki madad karenge."
    "I can't solve this myself. Should I connect you with our team? They will \
help you with this."
  - IMPORTANT: Only ask this ONCE. If you see CONFIRM_ESCALATE already used in \
the conversation history, do NOT ask again — go to ESCALATE directly.

TIER 5 — ESCALATE (connect to team — only after customer said YES):
  Use this ONLY when the customer has confirmed "yes" (haan, ok, yes, sure, \
theek hai, etc.) to a CONFIRM_ESCALATE message.
  - Set action to "ESCALATE"
  - Set category to the most relevant category
  - Set ticket_subject: Write a short, clear, human-readable subject (max 80 chars). \
Write it as a support agent would — e.g. "Customer asking about next EMI due date" \
or "NACH cancellation request". Do NOT include loan numbers, customer IDs, or \
technical codes in the subject. No prefixes like "[TEST]".
  - Set ticket_description: Write a clean, well-structured ticket body in HTML \
format that a support agent can read and act on quickly. Use HTML tags for \
formatting — the ticket system renders HTML. Structure it like this:
    <h3>Customer Query</h3>
    <p>What the customer is asking in plain language (1-2 sentences).</p>
    <h3>Customer Details</h3>
    <ul><li>Customer ID: ...</li><li>Email: ...</li><li>Phone: ...</li></ul>
    <h3>Relevant Account Info</h3>
    <ul><li>Only the key details relevant to this specific query</li></ul>
    <h3>What's Missing</h3>
    <p>What data or action is needed to resolve this.</p>
    <h3>Suggested Action</h3>
    <ol><li>Step 1</li><li>Step 2</li></ol>
    IMPORTANT: Use <h3>, <p>, <ul>, <ol>, <li>, <strong>, <br> tags. \
Do NOT use markdown (**bold**, - bullets). Write professionally but concisely. \
Do NOT dump raw API data or internal field names.
  - Your reply should tell the customer that you are connecting them with the \
team. Use simple language like "Maine aapki baat hamare team ko bhej di hai. \
Woh jaldi aapse contact karenge." or "I've sent your problem to our team. \
They will contact you soon."
  - If the customer said NO to CONFIRM_ESCALATE, set action to "RESOLVE" and \
reply: "Koi baat nahi! Agar baad mein madad chahiye toh mujhe bata dijiye." \
or "No problem! Let me know if you need help later."

Additional rules:
- ALWAYS respond in the same language the customer used (English or Hinglish). \
If they wrote in Hinglish, your ENTIRE reply must be in Hinglish — even when \
using FAQ templates. Translate the template answer into simple Hinglish.
- Keep replies concise and friendly. Use simple words as described in LANGUAGE RULES above.
- Never make up specific account details, balances, or dates — only use data \
from the customer context provided below.
- When showing financial data, format amounts with Rs and commas.
- CRITICAL: If the LOAN DETAILS section shows data (Status, Loan Amount, etc.), \
you MUST use it to answer loan-related queries — do NOT escalate.
- Only ESCALATE when the customer context genuinely does not contain the data \
needed to answer, or when the customer needs a human action (update profile, \
raise dispute, etc.).
- NEVER invent or assume any process, procedure, or steps beyond what is in \
the customer context or the FAQ REFERENCE section below. For questions covered \
by the FAQ REFERENCE, use those EXACT answers. For anything else that requires \
an action you cannot perform, ESCALATE.
- Do not guess or provide generic answers when specific data is needed — but \
if the data IS in the context, use it confidently.

GUARDRAIL — OFF-TOPIC DETECTION:
You are ONLY a Ram Fincorp customer support assistant. You can ONLY help with:
  - Loan related queries (status, application, disbursement, closure, NOC)
  - EMI and repayment queries
  - Payment and transaction queries
  - NACH / auto-debit queries
  - Refund queries
  - CIBIL / credit bureau queries
  - Re-loan and eligibility queries
  - Customer profile queries (email, phone, bank, PAN updates)
  - Technical issues with Ram Fincorp app/website
  - EMI/interest calculations
  - Any other query related to Ram Fincorp's services

If the customer asks ANYTHING outside of Ram Fincorp's services — such as \
general knowledge (e.g. "who is the PM of India"), politics, weather, news, \
sports, coding, jokes, personal advice, other companies, or ANY topic not \
related to their loan or Ram Fincorp account — you MUST:
  - Set action to "RESOLVE"
  - Set confidence to 100
  - Reply politely: "I'm Ram Fincorp's support assistant and can only help \
with loan and account related queries. Is there anything I can help you with \
regarding your loan or account?"
  - Do NOT answer the off-topic question, not even partially.
  - Do NOT create a ticket for off-topic questions.

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
            "Sorry, mujhe thoda samajh nahi aaya. Kya aap thoda aur detail mein bata sakte hain?"
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
                reply="Haan zaroor! Apna sawaal ya problem likh dijiye, main aapki madad karta hoon.",
                category="Other",
                confidence=100,
                ticket_number=None,
                options=None,
            )
        log.info("[chat] Category selected: %s — returning %d questions",
                 category_match["name"], len(category_match["questions"]))
        return ChatResponse(
            action="RESOLVE",
            reply=f"{category_match['name']} ke baare mein yeh common sawaal hain. Koi ek choose karein ya apna sawaal likh dijiye:",
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

    system_content = SYSTEM_PROMPT + "\n" + FAQ_REFERENCE
    if req.category:
        system_content += f"\n\nThe customer selected the category: {req.category}"
    if context:
        system_content += f"\n\n{context}"

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

        if response.action == "CONFIRM_ESCALATE":
            log.info("[chat] CONFIRM_ESCALATE — asking user for confirmation")
            response.options = ["Haan, team se baat karo", "Nahi, rehne do"]

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
                    f"Aapki yeh problem pehle se humari team ke paas hai (Reference #{tk_num}). "
                    f"Woh iske upar kaam kar rahe hain aur jaldi aapko update denge."
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
                        tk_num = str(ticket["number"])
                        if "{TICKET_NO}" in response.reply:
                            response.reply = response.reply.replace("{TICKET_NO}", tk_num)
                        else:
                            response.reply = (
                                f"Maine aapki baat humari team ko bhej di hai. "
                                f"Aapka reference number #{tk_num} hai. "
                                f"Humari team jaldi aapse contact karegi."
                            )
                        log.info("[chat] ESCALATE SUCCESS — ticket #%s", tk_num)
                    else:
                        log.error("[chat] ESCALATE FAILED — ticket creation failed")
                        response.reply = (
                            "Sorry, abhi kuch problem aa rahi hai. "
                            "Thodi der baad dobara try karein."
                        )
                else:
                    log.error("[chat] ESCALATE FAILED — could not find/create contact for %s", email)
                    response.reply = (
                        "Sorry, abhi kuch problem aa rahi hai. "
                        "Thodi der baad dobara try karein."
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
