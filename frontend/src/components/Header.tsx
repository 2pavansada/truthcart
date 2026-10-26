import React from "react";
import type { AuthUser } from "../api/types";

interface HeaderProps {
  onReset: () => void;
  isReady: boolean;
  authUser: AuthUser | null;
  onSignIn: () => void;
  onLogout: () => void;
  authBusy: boolean;
  authError: string | null;
}

export const Header: React.FC<HeaderProps> = ({
  onReset,
  isReady,
  authUser,
  onSignIn,
  onLogout,
  authBusy,
  authError,
}) => {
  return (
    <header className="app-header">
      <div className="brand">
        <div className="brand-icon">🛒</div>
        <div className="brand-text">
          <h1>TruthCart AI</h1>
          <div className="brand-tagline">
            <span>The Assistant That Doesn't Lie</span>
            <span className="verified-badge">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="20 6 9 17 4 12"></polyline>
              </svg>
              Verified Factual
            </span>
          </div>
        </div>
      </div>

      <div className="header-actions">
        <div
          title={isReady ? "Backend and catalogue index ready" : "Connecting..."}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "0.35rem",
            fontSize: "0.75rem",
            color: isReady ? "var(--emerald)" : "var(--amber)",
          }}
        >
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              backgroundColor: isReady ? "var(--emerald)" : "var(--amber)",
              boxShadow: isReady ? "0 0 8px var(--emerald)" : "none",
            }}
          />
          {isReady ? "Online" : "Connecting"}
        </div>

        {authUser ? (
          <>
            <span className="account-label" title={authUser.email}>Hi, {authUser.name}</span>
            <button className="btn-secondary" onClick={onLogout} disabled={authBusy}>
              {authBusy ? "Signing out…" : "Log out"}
            </button>
          </>
        ) : (
          <button className="btn-secondary" onClick={onSignIn}>
            Sign in
          </button>
        )}

        <button className="btn-secondary" onClick={onReset} title="Clear session filters and start new conversation">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"></path>
            <path d="M21 3v5h-5"></path>
            <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"></path>
            <path d="M8 16H3v5"></path>
          </svg>
          New Chat
        </button>
      </div>
      {authError && <div className="header-auth-error" role="alert">{authError}</div>}
    </header>
  );
};
