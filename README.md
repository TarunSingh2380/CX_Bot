# Ram Fincorp — Customer Support Chatbot

A two-screen customer support chatbot for **Ram Fincorp**, a loan & personal
finance company. React frontend + FastAPI backend, powered by GPT-5 Mini.

The bot classifies every customer message into one of three actions:

- **RESOLVE** — answers directly from its knowledge.
- **CLARIFY** — asks one follow-up question when the query is vague.
- **ESCALATE** — routes to the human CX team with a category, shown to the
  customer as a highlighted card.

## Project structure

```
UIBot/
├── backend/
│   ├── main.py            # FastAPI app, /chat endpoint + LLM logic
│   └── requirements.txt
├── frontend/              # React (Vite) app on port 3000
│   ├── src/App.jsx        # Chat UI
│   ├── src/index.css
│   └── ...
├── .env                   # OPENAI_API_KEY (not committed)
└── .env.example
```

## Setup

### 1. Add your API key

Edit `.env` in the project root:

```
OPENAI_API_KEY=sk-...
```

### 2. Backend (port 8000)

A `venv/` already exists in the project root. From the project root:

```powershell
# Activate the virtualenv
.\venv\Scripts\Activate.ps1

# Install dependencies
pip install -r backend/requirements.txt

# Run the server
cd backend
uvicorn main:app --reload --port 8000
```

Backend will be at http://localhost:8000 (health check at `/`).

### 3. Frontend (port 3000)

Requires **Node.js** (install from https://nodejs.org if `node` is not found).

```powershell
cd frontend
npm install
npm run dev
```

Opens http://localhost:3000 automatically.

## API

### `POST /chat`

Request:

```json
{
  "message": "Mera EMI kab due hai?",
  "conversation_history": [
    { "role": "user", "content": "..." },
    { "role": "assistant", "content": "..." }
  ]
}
```

Response:

```json
{
  "reply": "bot reply text",
  "action": "RESOLVE | ESCALATE | CLARIFY",
  "category": "category name or null",
  "confidence": 0
}
```

### Escalation categories

1. Payment Dispute
2. Waiver / Penalty Request
3. Loan Closure / NOC
4. Disbursement Issue
5. Legal / Harassment Complaint
6. Account / App Issue
7. Other / Complex Query

## Customer data enrichment

When the frontend sends an `email` and/or `phone` with a chat request, the
backend enriches the LLM prompt with that customer's real context (fetched
**once per customer and cached for 5 minutes** so we don't hammer the APIs):

| Source | Module | What it adds |
| --- | --- | --- |
| Customer profile API (custom) | `customer_api.py` | Name + `lead_id` |
| Transactions API (custom) | `customer_api.py` | Last 5 transactions |
| Zoho Desk | `zoho.py` | All **open** tickets with **full thread content** |

`enrichment.py` orchestrates all three and renders a context block that is
appended to the system prompt. Every source fails soft — if an API is down or
unconfigured, chat still works, just without that data.

### Zoho Desk setup

The Zoho client resolves the customer's email → contact → open tickets → full
thread bodies. To enable it, set these in `.env`:

```
ZOHO_DC=in                 # your data center: com | in | eu
ZOHO_ORG_ID=...            # Desk → Setup → Developer Space → API
ZOHO_CLIENT_ID=...
ZOHO_CLIENT_SECRET=...
ZOHO_REFRESH_TOKEN=...
```

To get the OAuth credentials, create a **Self Client** at
https://api-console.zoho.com with scope `Desk.tickets.READ,Desk.contacts.READ`,
generate a grant code, and exchange it once for a refresh token. The backend
then auto-mints hourly access tokens from that refresh token.

### Custom APIs (not yet deployed)

`customer_api.py` has both endpoints stubbed and env-driven. Once your APIs are
live, set `CUSTOMER_API_URL` / `TRANSACTIONS_API_URL` (+ `CUSTOMER_API_KEY`) in
`.env`, and adjust the request shape / response parsing in the `TODO`-marked
spots to match your real payloads. Until then those functions return `None` and
the bot simply runs without profile/transaction data.

## Notes

- Conversation history is kept on the frontend and sent with every request so
  the LLM always has full context.
- If the LLM returns malformed JSON, the backend returns a safe fallback
  `CLARIFY` response instead of erroring.
- CORS is enabled for `localhost:3000`.
- The model used is `gpt-5-mini` via the OpenAI Python SDK.
```
