import React, { useState, useEffect, useRef, useCallback } from "react";
import { Header } from "./components/Header";
import { AuthPage } from "./components/AuthPage";
import { ActiveConstraintsBar } from "./components/ActiveConstraintsBar";
import { AnswerText } from "./components/AnswerText";
import {
  postChat,
  resetSession,
  getProduct,
  getPolicySection,
  getHealth,
  getAuthMe,
  logoutAccount,
} from "./api/client";
import type {
  AuthUser,
  ChatResponse,
  ConstraintView,
  ConstraintOpIn,
  ProductCard,
  ClarificationOption,
  ProductDetail,
  PolicySectionDetail,
} from "./api/types";
import { resolveSearchEvidence, type SearchEvidenceDrawerState } from "./utils/searchEvidence";
import type { AuthMode } from "./utils/authValidation";

// ─── Types ───────────────────────────────────────────────────────────────────

interface Message {
  id: string;
  role: "user" | "assistant";
  text: string;
  response?: ChatResponse;
  pending?: boolean;
}

type DrawerContent =
  | { kind: "product"; data: ProductDetail }
  | { kind: "policy"; data: PolicySectionDetail }
  | { kind: "loading" }
  | SearchEvidenceDrawerState
  | null;

// ─── Helpers ─────────────────────────────────────────────────────────────────

function uid(): string {
  return Math.random().toString(36).slice(2, 10);
}

const QUICK_PROMPTS = [
  "Show me running shoes under ₹2000",
  "What's your return policy?",
  "Do you have wireless earphones?",
  "Compare laptop bags",
  "Any offers on electronics?",
  "Show in-stock watches under ₹5000",
];

const WELCOME_SUGGESTIONS = [
  { label: "👟 Running shoes under ₹2000", msg: "Show me running shoes under 2000" },
  { label: "📦 Return policy", msg: "What's your return policy?" },
  { label: "🎧 Wireless earphones", msg: "Do you have wireless earphones in stock?" },
  { label: "💻 Laptop bags", msg: "Show laptop bags" },
  { label: "⌚ Watches under ₹5000", msg: "In-stock watches under 5000" },
  { label: "👕 Men's clothing", msg: "Show me men's clothing" },
];

// ─── Sub-components ───────────────────────────────────────────────────────────

const DecisionBadge: React.FC<{ decision: string; ms?: number }> = ({ decision, ms }) => {
  const cls =
    decision === "ANSWER" ? "answer" : decision === "CLARIFY" ? "clarify" : "refuse";
  const icon = decision === "ANSWER" ? "✓" : decision === "CLARIFY" ? "?" : "✕";
  return (
    <div className="decision-header">
      <span className={`decision-badge ${cls}`}>
        {icon} {decision}
      </span>
      {ms !== undefined && (
        <span className="timing-meta">{ms}ms</span>
      )}
    </div>
  );
};

const StockBadge: React.FC<{ status: string; label?: string }> = ({ status, label }) => {
  const cls =
    status === "IN_STOCK" ? "in-stock" : status === "OUT_OF_STOCK" ? "out-of-stock" : "unknown";
  return (
    <span className={`card-stock-badge ${cls}`}>
      {label || (status === "IN_STOCK" ? "In Stock" : status === "OUT_OF_STOCK" ? "Out of Stock" : "Unknown")}
    </span>
  );
};

const ProductCardItem: React.FC<{
  card: ProductCard;
  onOpenProduct: (id: string) => void;
}> = ({ card, onOpenProduct }) => {
  // Find price & stock facts
  const priceFact = card.facts.find((f) => f.field === "price");
  const stockFact = card.facts.find((f) => f.field === "stock");

  return (
    <div className="product-card" onClick={() => onOpenProduct(card.product_id)} role="button" tabIndex={0}
      onKeyDown={(e) => e.key === "Enter" && onOpenProduct(card.product_id)}
      aria-label={`View details for ${card.title}`}>
      <div className="card-top">
        <div className="card-title">{card.title}</div>
        <div className="card-id-pill">{card.product_id}</div>
      </div>

      <div className="card-facts">
        <div className="card-price-row">
          <span className="card-price">
            {priceFact?.available ? priceFact.display : "—"}
          </span>
          {stockFact && (
            <StockBadge status={String(stockFact.value ?? "UNKNOWN")} label={stockFact.display} />
          )}
        </div>
        <div className="card-meta-row">
          <span>📦 {card.origin === "SEARCH_RESULT" ? "Search Result" : card.origin}</span>
        </div>
      </div>

      <button
        className="card-btn-evidence"
        onClick={(e) => { e.stopPropagation(); onOpenProduct(card.product_id); }}
        aria-label={`See evidence for ${card.title}`}
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line>
        </svg>
        View Evidence
      </button>
    </div>
  );
};

const ClarifyBox: React.FC<{
  clarificationId: string;
  reason: string;
  options: ClarificationOption[];
  onSelect: (clarificationId: string, optionId: string) => void;
}> = ({ clarificationId, reason, options, onSelect }) => (
  <div className="clarify-box" role="group" aria-label="Clarification needed">
    <div className="clarify-title">🤔 {reason}</div>
    <div className="clarify-options">
      {options.map((opt) => (
        <button
          key={opt.option_id}
          id={`clarify-${opt.option_id}`}
          className="clarify-btn"
          onClick={() => onSelect(clarificationId, opt.option_id)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  </div>
);

const SuggestionsBox: React.FC<{
  suggestions: ChatResponse["suggestions"];
  onSuggest: (ops: ConstraintOpIn[]) => void;
}> = ({ suggestions, onSuggest }) => {
  if (!suggestions || suggestions.length === 0) return null;
  return (
    <div className="suggestions-box">
      <div className="suggestions-title">💡 You might also try:</div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
        {suggestions.map((s) => (
          <button
            key={s.claim_id}
            className="suggestion-pill"
            onClick={() => onSuggest(s.constraint_ops)}
          >
            {s.label}
          </button>
        ))}
      </div>
    </div>
  );
};

const ThinkingBubble: React.FC = () => (
  <div className="message-row assistant" aria-live="polite" aria-label="TruthCart is thinking">
    <div className="assistant-card" style={{ padding: "1rem 1.25rem" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
        <div className="thinking-dots">
          <span /><span /><span />
        </div>
        <span style={{ color: "var(--text-muted)", fontSize: "0.85rem" }}>Verifying facts…</span>
      </div>
    </div>
  </div>
);

const EvidenceDrawer: React.FC<{
  content: DrawerContent;
  onClose: () => void;
}> = ({ content, onClose }) => {
  if (!content) return null;

  return (
    <div className="drawer-backdrop" role="dialog" aria-modal="true" aria-label="Evidence Details"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="drawer-panel">
        <div className="drawer-header">
          <div className="drawer-title">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
              <line x1="16" y1="13" x2="8" y2="13" />
              <line x1="16" y1="17" x2="8" y2="17" />
              <polyline points="10 9 9 9 8 9" />
            </svg>
            Evidence
          </div>
          <button className="drawer-close" onClick={onClose} aria-label="Close evidence drawer">✕</button>
        </div>

        <div className="drawer-body">
          {content.kind === "loading" && (
            <div style={{ color: "var(--text-muted)", textAlign: "center", padding: "2rem" }}>
              Loading…
            </div>
          )}

          {content.kind === "error" && (
            <div className="evidence-load-error" role="alert">
              <strong>Evidence could not be opened</strong>
              <span>{content.message}</span>
              <span>Close this panel and try the citation or product again.</span>
            </div>
          )}

          {content.kind === "product" && (
            <>
              <div className="evidence-field-card" style={{ borderColor: "var(--border-accent)" }}>
                <div className="field-name">Product ID</div>
                <div className="field-value" style={{ fontWeight: 700 }}>{content.data.product_id}</div>
              </div>
              <div className="evidence-field-card">
                <div className="field-name">Title</div>
                <div className="field-value">{content.data.title}</div>
              </div>
              <div className="evidence-field-card">
                <div className="field-name">Category</div>
                <div className="field-value">{content.data.category ?? "—"}</div>
              </div>
              <div className="evidence-field-card">
                <div className="field-name">Price</div>
                <div className="field-value" style={{ color: "var(--emerald)", fontWeight: 700 }}>
                  {content.data.price_display ?? "—"}
                </div>
              </div>
              <div className="evidence-field-card">
                <div className="field-name">Stock Status</div>
                <div className="field-value">
                  <StockBadge status={content.data.stock_status} label={content.data.stock_label} />
                </div>
              </div>
              {content.data.rating !== null && (
                <div className="evidence-field-card">
                  <div className="field-name">Rating</div>
                  <div className="field-value">⭐ {content.data.rating} ({content.data.rating_count} reviews)</div>
                </div>
              )}
              {content.data.description && (
                <div className="evidence-field-card">
                  <div className="field-name">Description</div>
                  <div className="field-value" style={{ fontSize: "0.9rem", lineHeight: 1.55, color: "#cbd5e1" }}>
                    {content.data.description}
                  </div>
                </div>
              )}
              {content.data.attributes.length > 0 && (
                <div className="evidence-field-card">
                  <div className="field-name">Attributes</div>
                  <div style={{ display: "flex", flexDirection: "column", gap: "0.35rem", marginTop: "0.25rem" }}>
                    {content.data.attributes.map((a, i) => (
                      <div key={i} style={{ display: "flex", justifyContent: "space-between", fontSize: "0.85rem" }}>
                        <span style={{ color: "var(--text-secondary)" }}>{a.key_raw}</span>
                        <span style={{ color: "#f8fafc" }}>{a.value_raw}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              {content.data.unavailable_fields.length > 0 && (
                <div className="evidence-field-card" style={{ borderColor: "var(--rose-glow)" }}>
                  <div className="field-name" style={{ color: "var(--rose)" }}>Unavailable Fields</div>
                  <div className="field-value" style={{ color: "#fda4af", fontSize: "0.85rem" }}>
                    {content.data.unavailable_fields.join(", ")}
                  </div>
                </div>
              )}
            </>
          )}

          {content.kind === "policy" && (
            <>
              <div className="evidence-field-card" style={{ borderColor: "var(--amber-glow)" }}>
                <div className="field-name">Section</div>
                <div className="field-value" style={{ fontWeight: 700 }}>{content.data.section_id}</div>
              </div>
              <div className="evidence-field-card">
                <div className="field-name">Heading</div>
                <div className="field-value">{content.data.heading}</div>
              </div>
              <div className="evidence-field-card">
                <div className="field-name">Policy Text</div>
                <div className="field-value" style={{ fontSize: "0.9rem", lineHeight: 1.65, color: "#cbd5e1", whiteSpace: "pre-wrap" }}>
                  {content.data.body}
                </div>
              </div>
            </>
          )}

          {content.kind === "search" && (
            <>
              <div className="evidence-field-card">
                <div className="field-name">Search ID</div>
                <div className="field-value">{content.data.search_id}</div>
              </div>
              <div className="evidence-field-card">
                <div className="field-name">Search Scope</div>
                <div className="field-value">
                  {content.data.scope === "CATALOGUE" ? "Product catalogue" : "Policy sections"}
                </div>
              </div>
              <div className="evidence-field-card">
                <div className="field-name">Search Completeness</div>
                <div className="field-value">
                  {content.data.completeness === "EXHAUSTIVE" ? "Complete scan" : "Incomplete scan"}
                </div>
              </div>

              {content.data.scope === "CATALOGUE" ? (
                <>
                  <div className="evidence-field-card">
                    <div className="field-name">Query</div>
                    <div className="field-value">{content.data.query_text}</div>
                  </div>
                  <div className="evidence-field-card">
                    <div className="field-name">Results</div>
                    <div className="field-value">
                      {content.data.exact_match_count} exact matches · {content.data.returned_count} returned
                    </div>
                  </div>
                  {content.data.applied_constraints.length > 0 && (
                    <div className="evidence-field-card">
                      <div className="field-name">Applied Filters</div>
                      <div className="field-value">
                        {content.data.applied_constraints.map((constraint) => constraint.label).join(", ")}
                      </div>
                    </div>
                  )}
                  {content.data.incomplete_reasons.length > 0 && (
                    <div className="evidence-field-card">
                      <div className="field-name">Why the Search Is Incomplete</div>
                      <div className="field-value">{content.data.incomplete_reasons.join(", ")}</div>
                    </div>
                  )}
                </>
              ) : (
                <>
                  <div className="evidence-field-card">
                    <div className="field-name">Query Terms</div>
                    <div className="field-value">{content.data.query_terms.join(", ") || "—"}</div>
                  </div>
                  <div className="evidence-field-card">
                    <div className="field-name">Sections Checked</div>
                    <div className="field-value">{content.data.sections_checked.join(", ") || "—"}</div>
                  </div>
                  <div className="evidence-field-card">
                    <div className="field-name">Matched Sections</div>
                    <div className="field-value">{content.data.matched_section_ids.join(", ") || "—"}</div>
                  </div>
                  {content.data.incomplete_reasons.length > 0 && (
                    <div className="evidence-field-card">
                      <div className="field-name">Why the Search Is Incomplete</div>
                      <div className="field-value">{content.data.incomplete_reasons.join(", ")}</div>
                    </div>
                  )}
                </>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
};

const WelcomeScreen: React.FC<{ onPrompt: (msg: string) => void }> = ({ onPrompt }) => (
  <div className="welcome-screen">
    <div className="welcome-badge">
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="20 6 9 17 4 12"></polyline>
      </svg>
      AI-Verified • No Hallucinations
    </div>
    <div className="welcome-icon">🛒</div>
    <h2 className="welcome-heading">TruthCart AI</h2>
    <p className="welcome-subtext">
      Ask me anything about our products and policies. Every claim I make is backed by verified evidence from our catalogue.
    </p>
    <div className="welcome-grid">
      {WELCOME_SUGGESTIONS.map((s, i) => (
        <button
          key={i}
          id={`welcome-suggestion-${i}`}
          className="welcome-card"
          onClick={() => onPrompt(s.msg)}
        >
          {s.label}
        </button>
      ))}
    </div>
  </div>
);

// ─── Main App ─────────────────────────────────────────────────────────────────

export default function App() {
  const [sessionId] = useState<string>(() => `sess_${uid()}`);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [isReady, setIsReady] = useState(false);
  const [constraints, setConstraints] = useState<ConstraintView[]>([]);
  const [drawer, setDrawer] = useState<DrawerContent>(null);
  const [authUser, setAuthUser] = useState<AuthUser | null>(null);
  const [authView, setAuthView] = useState<AuthMode | null>(null);
  const [authBusy, setAuthBusy] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [, setPendingClarificationId] = useState<string | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Health check on mount
  useEffect(() => {
    getHealth()
      .then(() => setIsReady(true))
      .catch(() => setIsReady(false));
  }, []);

  // The HttpOnly session cookie is never read by the frontend; /me restores account state.
  useEffect(() => {
    let active = true;
    getAuthMe()
      .then((user) => {
        if (!active) return;
        setAuthUser(user);
        setAuthError(null);
      })
      .catch(() => {
        if (active) setAuthError("Account status could not be checked. Guest shopping is still available.");
      });
    return () => { active = false; };
  }, []);

  // Scroll to bottom on new message
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const sendMessage = useCallback(
    async (text: string, constraintOps?: ConstraintOpIn[], resolution?: { clarification_id: string; option_id: string }) => {
      if (!text.trim() && !constraintOps?.length && !resolution) return;

      const userMsgId = uid();
      const thinkingId = uid();

      setMessages((prev) => [
        ...prev,
        ...(text.trim() ? [{ id: userMsgId, role: "user" as const, text }] : []),
        { id: thinkingId, role: "assistant" as const, text: "", pending: true },
      ]);
      setInput("");
      setLoading(true);

      try {
        const response = await postChat({
          session_id: sessionId,
          message: text,
          constraint_ops: constraintOps,
          resolution,
          options: { max_results: 5 },
        });

        setConstraints(response.active_constraints || []);
        if (response.clarification) {
          setPendingClarificationId(response.clarification.clarification_id);
        } else {
          setPendingClarificationId(null);
        }

        setMessages((prev) =>
          prev.map((m) =>
            m.id === thinkingId
              ? { id: thinkingId, role: "assistant" as const, text: response.answer_text, response }
              : m
          )
        );
      } catch (err: any) {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === thinkingId
              ? {
                  id: thinkingId,
                  role: "assistant" as const,
                  text: `⚠️ ${err.message || "Something went wrong. Please try again."}`,
                }
              : m
          )
        );
      } finally {
        setLoading(false);
        setTimeout(() => inputRef.current?.focus(), 50);
      }
    },
    [sessionId]
  );

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!loading && input.trim()) {
      sendMessage(input.trim());
    }
  };

  const handleQuickPrompt = (msg: string) => {
    setInput(msg);
    sendMessage(msg);
  };

  const handleClarify = (clarificationId: string, optionId: string) => {
    sendMessage("", undefined, { clarification_id: clarificationId, option_id: optionId });
  };

  const handleRemoveConstraint = (op: ConstraintOpIn) => {
    sendMessage("", [op]);
  };

  const handleSuggestion = (ops: ConstraintOpIn[]) => {
    sendMessage("", ops);
  };

  const handleOpenEvidence = async (
    kind: "product" | "policy" | "search",
    id: string,
    responseEvidence?: ChatResponse["evidence"],
  ) => {
    if (kind === "search") {
      setDrawer(resolveSearchEvidence(responseEvidence, id));
      return;
    }

    setDrawer({ kind: "loading" });
    try {
      if (kind === "product") {
        const data = await getProduct(id);
        setDrawer({ kind: "product", data });
      } else {
        const data = await getPolicySection(id);
        setDrawer({ kind: "policy", data });
      }
    } catch {
      setDrawer({
        kind: "error",
        message: "Check your connection and try opening this evidence again.",
      });
    }
  };

  const handleReset = async () => {
    await resetSession(sessionId).catch(() => {});
    setMessages([]);
    setConstraints([]);
    setPendingClarificationId(null);
    setInput("");
  };

  const handleLogout = async () => {
    if (authBusy) return;
    setAuthBusy(true);
    setAuthError(null);
    try {
      await logoutAccount();
      setAuthUser(null);
    } catch {
      setAuthError("Sign out could not be completed. Please try again.");
    } finally {
      setAuthBusy(false);
    }
  };

  const charCount = input.length;
  const maxChars = 500;

  return (
    <div className="app-layout">
      <Header
        onReset={handleReset}
        isReady={isReady}
        authUser={authUser}
        onSignIn={() => { setAuthError(null); setAuthView("login"); }}
        onLogout={handleLogout}
        authBusy={authBusy}
        authError={authError}
      />
      {authView && (
        <AuthPage
          mode={authView}
          onModeChange={setAuthView}
          onAuthenticated={(user) => {
            setAuthUser(user);
            setAuthView(null);
            setAuthError(null);
          }}
          onContinueAsGuest={() => setAuthView(null)}
        />
      )}
      <ActiveConstraintsBar constraints={constraints} onRemove={handleRemoveConstraint} />

      <div className="chat-container" role="log" aria-label="Conversation" aria-live="polite">
        {messages.length === 0 ? (
          <WelcomeScreen onPrompt={handleQuickPrompt} />
        ) : (
          messages.map((msg) => {
            if (msg.role === "user") {
              return (
                <div key={msg.id} className="message-row user">
                  <div className="user-bubble">{msg.text}</div>
                </div>
              );
            }

            if (msg.pending) {
              return <ThinkingBubble key={msg.id} />;
            }

            const resp = msg.response;
            return (
              <div key={msg.id} className="message-row assistant">
                <div className="assistant-card">
                  {resp && (
                    <DecisionBadge decision={resp.decision} ms={resp.metrics?.total_ms} />
                  )}

                  <AnswerText
                    text={msg.text}
                    onOpenEvidence={(kind, id) => handleOpenEvidence(kind, id, resp?.evidence)}
                  />

                  {resp?.products && resp.products.length > 0 && (
                    <div className="product-grid">
                      {resp.products.map((card) => (
                        <ProductCardItem
                          key={card.product_id}
                          card={card}
                          onOpenProduct={(id) => handleOpenEvidence("product", id)}
                        />
                      ))}
                    </div>
                  )}

                  {resp?.clarification && (
                    <ClarifyBox
                      clarificationId={resp.clarification.clarification_id}
                      reason={resp.clarification.reason}
                      options={resp.clarification.options}
                      onSelect={handleClarify}
                    />
                  )}

                  {resp?.refusal && (
                    <div className="refusal-box" role="alert">
                      🚫 {resp.refusal.reason.replace(/_/g, " ")}
                    </div>
                  )}

                  {resp?.suggestions && resp.suggestions.length > 0 && (
                    <SuggestionsBox suggestions={resp.suggestions} onSuggest={handleSuggestion} />
                  )}
                </div>
              </div>
            );
          })
        )}
        <div ref={bottomRef} />
      </div>

      {messages.length === 0 && (
        <div className="quick-prompts" aria-label="Quick prompt suggestions">
          {QUICK_PROMPTS.map((p, i) => (
            <button key={i} className="quick-btn" id={`quick-prompt-${i}`} onClick={() => handleQuickPrompt(p)}>
              {p}
            </button>
          ))}
        </div>
      )}

      <div className="composer-area">
        <form className="composer-form" onSubmit={handleSubmit} role="search" aria-label="Ask TruthCart AI">
          <div className="composer-input-wrapper">
            <input
              ref={inputRef}
              id="chat-input"
              className="composer-input"
              type="text"
              placeholder="Ask about products, prices, availability, policies…"
              value={input}
              onChange={(e) => setInput(e.target.value.slice(0, maxChars))}
              disabled={loading}
              aria-label="Your message"
              autoComplete="off"
              autoFocus
            />
            <span className={`char-counter ${charCount > maxChars * 0.85 ? "warn" : ""}`}>
              {charCount}/{maxChars}
            </span>
          </div>
          <button
            id="btn-send"
            type="submit"
            className="btn-send"
            disabled={loading || !input.trim()}
            aria-label="Send message"
          >
            {loading ? (
              <span className="spinner" aria-hidden="true" />
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <line x1="22" y1="2" x2="11" y2="13" />
                <polygon points="22 2 15 22 11 13 2 9 22 2" />
              </svg>
            )}
            Send
          </button>
        </form>
      </div>

      <EvidenceDrawer content={drawer} onClose={() => setDrawer(null)} />
    </div>
  );
}
