import React, { useEffect, useState } from "react";
import { loginAccount, signupAccount } from "../api/client";
import type { AuthUser } from "../api/types";
import { validateAuthForm, type AuthMode } from "../utils/authValidation";

interface AuthPageProps {
  mode: AuthMode;
  onModeChange: (mode: AuthMode) => void;
  onAuthenticated: (user: AuthUser) => void;
  onContinueAsGuest: () => void;
}

export const AuthPage: React.FC<AuthPageProps> = ({
  mode,
  onModeChange,
  onAuthenticated,
  onContinueAsGuest,
}) => {
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [passwordConfirmation, setPasswordConfirmation] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    setError("");
    setPassword("");
    setPasswordConfirmation("");
    setShowPassword(false);
  }, [mode]);

  const handleSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) return;

    const validationError = validateAuthForm(mode, { name, email, password, passwordConfirmation });
    if (validationError) {
      setError(validationError);
      return;
    }

    setError("");
    setBusy(true);
    try {
      const user = mode === "signup"
        ? await signupAccount({ name: name.trim(), email: email.trim(), password })
        : await loginAccount({ email: email.trim(), password });
      onAuthenticated(user);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Authentication failed. Please try again.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="auth-screen" aria-label={mode === "signup" ? "Create account" : "Sign in"}>
      <section className="auth-card">
        <div className="auth-brand-mark" aria-hidden="true">🛒</div>
        <p className="auth-eyebrow">TruthCart AI</p>
        <h2>{mode === "signup" ? "Create your account" : "Welcome back"}</h2>
        <p className="auth-intro">
          {mode === "signup"
            ? "Save your account for your next visit. Shopping remains open to guests too."
            : "Sign in to your TruthCart account."}
        </p>

        <form className="auth-form" onSubmit={handleSubmit} noValidate>
          {mode === "signup" && (
            <label className="auth-field">
              <span>Name</span>
              <input
                autoComplete="name"
                maxLength={80}
                value={name}
                onChange={(event) => setName(event.target.value)}
                required
              />
            </label>
          )}
          <label className="auth-field">
            <span>Email</span>
            <input
              type="email"
              autoComplete="email"
              maxLength={254}
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
            />
          </label>
          <label className="auth-field">
            <span>Password</span>
            <div className="auth-password-input">
              <input
                type={showPassword ? "text" : "password"}
                autoComplete={mode === "signup" ? "new-password" : "current-password"}
                maxLength={128}
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                required
              />
              <button
                className="auth-show-password"
                type="button"
                onClick={() => setShowPassword((visible) => !visible)}
                aria-label={showPassword ? "Hide password" : "Show password"}
              >
                {showPassword ? "Hide" : "Show"}
              </button>
            </div>
          </label>
          {mode === "signup" && (
            <label className="auth-field">
              <span>Confirm password</span>
              <input
                type={showPassword ? "text" : "password"}
                autoComplete="new-password"
                maxLength={128}
                value={passwordConfirmation}
                onChange={(event) => setPasswordConfirmation(event.target.value)}
                required
              />
            </label>
          )}

          {error && <div className="auth-error" role="alert">{error}</div>}

          <button className="auth-submit" type="submit" disabled={busy}>
            {busy ? <><span className="spinner" aria-hidden="true" /> Please wait…</> : mode === "signup" ? "Create account" : "Sign in"}
          </button>
        </form>

        <p className="auth-switch">
          {mode === "signup" ? "Already have an account?" : "New to TruthCart?"}{" "}
          <button type="button" onClick={() => onModeChange(mode === "signup" ? "login" : "signup")}>
            {mode === "signup" ? "Sign in" : "Create an account"}
          </button>
        </p>
        <button className="auth-guest" type="button" onClick={onContinueAsGuest}>
          Continue as a guest
        </button>
      </section>
    </main>
  );
};
