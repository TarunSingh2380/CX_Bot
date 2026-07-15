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
    setIsTyping(true);

    const history = messages.map((m) => ({ role: m.role, content: m.content }));

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
        escalation:
          data.action === "ESCALATE"
            ? { category: data.category || "Other / Complex Query" }
            : null,
        existingTicket:
          data.action === "EXISTING_TICKET"
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
              {["Check Loan Details", "View Payment History", "Request NOC", "Other Query"].map(
                (label) => (
                  <button
                    key={label}
                    className="sidebar-action-btn"
                    onClick={() => { sendMessage(label); setSidebarOpen(false); }}
                    disabled={isTyping}
                  >
                    {label}
                  </button>
                )
              )}
            </div>
          </div>
          <div className="sidebar-footer">
            <button
              className="logout-btn"
              onClick={() => {
                setCustomer(null);
                setMessages([]);
                setInput("");
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
            <div className="avatar">RF</div>
            <div className="header-info">
              <div className="header-title">Ram Fincorp Support</div>
              <div className="header-status">
                <span className="status-dot" />
                Online
              </div>
            </div>
          </header>

          <div className="chat" ref={scrollRef}>
            {messages.map((m, i) => (
              <div key={i} className={`row ${m.role}`}>
                {m.role === "assistant" && <div className="bot-avatar">RF</div>}
                <div className={`bubble ${m.role}`}>
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
                        &#128203; Existing ticket found
                      </div>
                      <div className="ticket-body">
                        We already have an open ticket
                        {m.existingTicket.ticketNumber &&
                          ` (#${m.existingTicket.ticketNumber})`}{" "}
                        for this issue. Our team is on it.
                      </div>
                    </div>
                  )}

                  {m.escalation && (
                    <div className="escalation-card">
                      <div className="escalation-title">
                        &#9888; Query escalated to CX team
                      </div>
                      <div className="escalation-body">
                        Your query has been categorised as{" "}
                        <strong>{m.escalation.category}</strong>. Our CX team
                        will reach out to you shortly.
                      </div>
                    </div>
                  )}
                </div>
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
              placeholder="Type your message..."
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
