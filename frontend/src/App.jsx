import { useEffect, useRef, useState } from "react";

const API_BASE = window.location.origin;


export default function App() {
  const [identifier, setIdentifier] = useState("");
  const [identifying, setIdentifying] = useState(false);
  const [identifyError, setIdentifyError] = useState("");
  const [customer, setCustomer] = useState(null);

  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState("");
  const [isTyping, setIsTyping] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const scrollRef = useRef(null);

  // Agent mode state (kept but disconnected from ESCALATE flow)
  const [agentMode, setAgentMode] = useState(false);
  const [conversationId, setConversationId] = useState(null);
  const [agentClosed, setAgentClosed] = useState(false);
  const lastSeenRef = useRef(0);
  const pollRef = useRef(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages, isTyping]);

  // Auto-send greeting when customer is identified.
  const greetedRef = useRef(false);
  useEffect(() => {
    if (customer && !greetedRef.current) {
      greetedRef.current = true;
      autoGreet();
    }
  }, [customer]);

  // Poll for agent messages when in agent mode
  useEffect(() => {
    if (!agentMode || !conversationId || agentClosed) {
      if (pollRef.current) clearInterval(pollRef.current);
      return;
    }

    async function poll() {
      try {
        const res = await fetch(`${API_BASE}/agent/poll`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            conversation_id: conversationId,
            last_seen: lastSeenRef.current,
          }),
        });
        if (!res.ok) return;
        const data = await res.json();

        if (data.messages && data.messages.length > 0) {
          const newMsgs = data.messages.map((m) => ({
            role: "agent",
            content: m.text,
            agentName: m.sender,
            sequenceId: m.sequence_id,
          }));
          setMessages((prev) => [...prev, ...newMsgs]);
          lastSeenRef.current = Math.max(
            ...data.messages.map((m) => m.sequence_id)
          );
        }

        if (data.closed) {
          setAgentClosed(true);
          setMessages((prev) => [
            ...prev,
            {
              role: "system",
              content: "The agent has ended the conversation. You can continue chatting with our bot.",
            },
          ]);
          setAgentMode(false);
          setConversationId(null);
          lastSeenRef.current = 0;
        }
      } catch {
        // polling error, will retry
      }
    }

    poll();
    pollRef.current = setInterval(poll, 3000);
    return () => clearInterval(pollRef.current);
  }, [agentMode, conversationId, agentClosed]);

  async function autoGreet() {
    setIsTyping(true);
    try {
      const res = await fetch(`${API_BASE}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: "hi",
          conversation_history: [],
          email: customer.email || customer.inputEmail,
          phone: customer.mobile || customer.inputPhone,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setMessages([
        {
          role: "assistant",
          content: data.reply,
          options: data.options || null,
        },
      ]);
    } catch {
      setMessages([
        {
          role: "assistant",
          content: "Welcome to Ram Fincorp support. How can I help you today?",
        },
      ]);
    } finally {
      setIsTyping(false);
    }
  }

  async function handleIdentify() {
    const val = identifier.trim();
    if (!val || identifying) return;
    setIdentifyError("");
    setIdentifying(true);

    const isEmail = val.includes("@");
    const body = isEmail ? { email: val } : { phone: val };

    try {
      const res = await fetch(`${API_BASE}/identify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (!data.found) {
        setIdentifyError(
          "No customer found with this email/phone. Please check and try again."
        );
        return;
      }
      setCustomer({
        ...data,
        inputEmail: isEmail ? val : null,
        inputPhone: isEmail ? null : val,
      });
    } catch {
      setIdentifyError("Could not connect to the server. Please try again.");
    } finally {
      setIdentifying(false);
    }
  }

  async function sendMessage(text) {
    const msg = (text || input).trim();
    if (!msg || isTyping) return;

    const userMsg = { role: "user", content: msg };
    setMessages((prev) => [...prev, userMsg]);
    setInput("");

    // Agent mode: forward to SalesIQ (kept but not triggered by ESCALATE now)
    if (agentMode && conversationId) {
      try {
        await fetch(`${API_BASE}/agent/send`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            conversation_id: conversationId,
            message: msg,
          }),
        });
      } catch {
        setMessages((prev) => [
          ...prev,
          {
            role: "system",
            content: "Could not send message to agent. Please try again.",
          },
        ]);
      }
      return;
    }

    // Bot mode: send to /chat
    setIsTyping(true);
    const history = messages.map((m) => ({
      role: m.role === "agent" ? "assistant" : m.role,
      content: m.content,
    })).filter((m) => m.role === "user" || m.role === "assistant");

    try {
      const res = await fetch(`${API_BASE}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: msg,
          conversation_history: history,
          email: customer.email || customer.inputEmail,
          phone: customer.mobile || customer.inputPhone,
        }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();

      const botMsg = {
        role: "assistant",
        content: data.reply,
        options: data.options || null,
        existingTicket:
          data.action === "EXISTING_TICKET"
            ? { ticketNumber: data.ticket_number }
            : null,
        ticketCreated:
          data.action === "ESCALATE" && data.ticket_number
            ? { ticketNumber: data.ticket_number }
            : null,
      };
      setMessages((prev) => [...prev, botMsg]);
    } catch {
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content:
            "Sorry, I'm having trouble connecting right now. Please try again in a moment.",
        },
      ]);
    } finally {
      setIsTyping(false);
    }
  }

  function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (!customer) handleIdentify();
      else sendMessage();
    }
  }

  // ======================== IDENTIFY SCREEN ========================
  if (!customer) {
    return (
      <div className="page">
        <div className="identify-container">
          <div className="identify-left">
            <div className="brand-logo">RF</div>
            <h1 className="brand-name">Ram Fincorp</h1>
            <p className="brand-tagline">
              Your trusted partner for personal loans and financial solutions
            </p>
            <div className="brand-features">
              <div className="feature">
                <span className="feature-icon">&#9889;</span>
                <span>Instant loan status updates</span>
              </div>
              <div className="feature">
                <span className="feature-icon">&#128176;</span>
                <span>EMI & payment history</span>
              </div>
              <div className="feature">
                <span className="feature-icon">&#128196;</span>
                <span>NOC & document requests</span>
              </div>
              <div className="feature">
                <span className="feature-icon">&#128172;</span>
                <span>24/7 support assistance</span>
              </div>
            </div>
          </div>

          <div className="identify-right">
            <div className="identify-card">
              <h2 className="identify-heading">Get Started</h2>
              <p className="identify-text">
                Enter your registered email or phone number to connect with our
                support team.
              </p>

              <div className="input-group">
                <label className="input-label">Email or Phone Number</label>
                <input
                  className="identify-input"
                  type="text"
                  placeholder="e.g. john@example.com or 9876543210"
                  value={identifier}
                  onChange={(e) => setIdentifier(e.target.value)}
                  onKeyDown={handleKeyDown}
                  disabled={identifying}
                  autoFocus
                />
              </div>

              {identifyError && (
                <div className="identify-error">{identifyError}</div>
              )}

              <button
                className="identify-btn"
                onClick={handleIdentify}
                disabled={identifying || !identifier.trim()}
              >
                {identifying ? (
                  <>
                    <span className="spinner" /> Verifying...
                  </>
                ) : (
                  "Continue to Chat"
                )}
              </button>

              <p className="identify-footer">
                We&apos;ll verify your identity to provide personalized support.
              </p>
            </div>
          </div>
        </div>
      </div>
    );
  }

  // ======================== CHAT SCREEN ========================
  return (
    <div className="page">
      <div className="chat-container">
        {sidebarOpen && (
          <div className="sidebar-overlay" onClick={() => setSidebarOpen(false)} />
        )}
        <aside className={`sidebar ${sidebarOpen ? "sidebar-open" : ""}`}>
          <div className="sidebar-brand">
            <div className="avatar-lg">RF</div>
            <span className="sidebar-title">Ram Fincorp</span>
          </div>
          <div className="sidebar-divider" />
          <div className="sidebar-section">
            <div className="sidebar-label">Customer</div>
            {customer.email && (
              <div className="sidebar-value">{customer.email}</div>
            )}
            {customer.mobile && (
              <div className="sidebar-value">{customer.mobile}</div>
            )}
            {customer.customerID && (
              <>
                <div className="sidebar-label" style={{ marginTop: 12 }}>
                  Customer ID
                </div>
                <div className="sidebar-value">{customer.customerID}</div>
              </>
            )}
            {customer.leadID && (
              <>
                <div className="sidebar-label" style={{ marginTop: 12 }}>
                  Lead ID
                </div>
                <div className="sidebar-value">{customer.leadID}</div>
              </>
            )}
          </div>
          <div className="sidebar-divider" />
          <div className="sidebar-section">
            <div className="sidebar-label">Quick Actions</div>
            <div className="sidebar-actions">
              {[
                "Loan Status",
                "EMI & Repayment",
                "NACH / Auto-Debit",
                "Loan Closure & NOC",
                "Refunds",
                "Cooling-Off Period",
                "Credit Bureau / CIBIL",
                "Re-Loan / Eligibility",
                "Customer Profile",
                "Technical Issues",
                "Payments & Transactions",
                "EMI & Interest Calculator",
                "Other",
              ].map((cat) => (
                <button
                  key={cat}
                  className="sidebar-action-btn"
                  onClick={() => {
                    setSidebarOpen(false);
                    sendMessage(cat);
                  }}
                  disabled={isTyping}
                >
                  {cat}
                </button>
              ))}
            </div>
          </div>
          <div className="sidebar-footer">
            <button
              className="logout-btn"
              onClick={() => {
                setCustomer(null);
                setMessages([]);
                setInput("");
                setAgentMode(false);
                setConversationId(null);
                setAgentClosed(false);
                lastSeenRef.current = 0;
                greetedRef.current = false;
              }}
            >
              Switch Customer
            </button>
          </div>
        </aside>

        {/* Main chat area */}
        <main className="chat-main">
          <header className="header">
            <button
              className="menu-btn"
              onClick={() => setSidebarOpen((v) => !v)}
              aria-label="Toggle menu"
            >
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
                <path d="M3 6h18M3 12h18M3 18h18" stroke="currentColor" strokeWidth="2" strokeLinecap="round"/>
              </svg>
            </button>
            <div className={`avatar ${agentMode ? "avatar-agent" : ""}`}>
              {agentMode ? "AG" : "RF"}
            </div>
            <div className="header-info">
              <div className="header-title">
                {agentMode ? "Live Agent" : "Ram Fincorp Support"}
              </div>
              <div className="header-status">
                <span className={`status-dot ${agentMode ? "status-dot-agent" : ""}`} />
                {agentMode ? "Connected to agent" : "Online"}
              </div>
            </div>
          </header>

          <div className="chat" ref={scrollRef}>
            {messages.map((m, i) => (
              <div key={i} className={`row ${m.role === "agent" ? "assistant" : m.role}`}>
                {(m.role === "assistant" || m.role === "agent") && (
                  <div className={`bot-avatar ${m.role === "agent" ? "agent-avatar" : ""}`}>
                    {m.role === "agent" ? "AG" : "RF"}
                  </div>
                )}
                {m.role === "system" ? (
                  <div className="system-msg">{m.content}</div>
                ) : (
                  <div className={`bubble ${m.role === "agent" ? "assistant agent-bubble" : m.role}`}>
                    {m.role === "agent" && m.agentName && (
                      <div className="agent-name">{m.agentName}</div>
                    )}
                    {m.content}

                    {m.options && (
                      <div className="quick-options">
                        {m.options.map((opt) => (
                          <button
                            key={opt}
                            className="quick-opt-btn"
                            onClick={() => sendMessage(opt)}
                            disabled={isTyping}
                          >
                            {opt}
                          </button>
                        ))}
                      </div>
                    )}

                    {m.existingTicket && (
                      <div className="ticket-card">
                        <div className="ticket-title">
                          Existing ticket found
                        </div>
                        <div className="ticket-body">
                          We already have an open ticket
                          {m.existingTicket.ticketNumber &&
                            ` (#${m.existingTicket.ticketNumber})`}{" "}
                          for this issue. Our team is on it.
                        </div>
                      </div>
                    )}

                    {m.ticketCreated && (
                      <div className="ticket-created-card">
                        <div className="ticket-created-title">
                          Support Ticket Created
                        </div>
                        <div className="ticket-created-body">
                          Ticket <strong>#{m.ticketCreated.ticketNumber}</strong> has been
                          created. Our team will review and get back to you.
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>
            ))}

            {isTyping && (
              <div className="row assistant">
                <div className="bot-avatar">RF</div>
                <div className="bubble assistant typing">
                  <span className="dot" />
                  <span className="dot" />
                  <span className="dot" />
                </div>
              </div>
            )}
          </div>

          <div className="composer">
            <input
              className="composer-input"
              type="text"
              placeholder={agentMode ? "Type a message to the agent..." : "Type your message..."}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={isTyping}
            />
            <button
              className="send-btn"
              onClick={() => sendMessage()}
              disabled={isTyping || !input.trim()}
              title="Send"
            >
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
                <path
                  d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z"
                  fill="currentColor"
                />
              </svg>
            </button>
          </div>
        </main>
      </div>
    </div>
  );
}
