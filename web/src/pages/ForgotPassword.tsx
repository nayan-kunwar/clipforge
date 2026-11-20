import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";

export function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [devUrl, setDevUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setMessage(null);
    setDevUrl(null);
    try {
      const response = await api.auth.forgotPassword(email);
      setMessage(response.message);
      if (response.dev_reset_url) setDevUrl(response.dev_reset_url);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Request failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="auth-page">
      <form className="auth-card" onSubmit={submit}>
        <div className="brand">
          <span className="brand-mark">C</span>
          <span>ClipForge</span>
        </div>
        <div>
          <h1>Reset password</h1>
          <p className="muted small" style={{ marginTop: 6 }}>
            We will email you a reset link if the account exists.
          </p>
        </div>
        <div className="field">
          <label>Email</label>
          <input
            className="input"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
          />
        </div>
        {message ? <div className="success-text">{message}</div> : null}
        {devUrl ? (
          <div className="small muted">
            Development reset link: <a href={devUrl}>{devUrl}</a>
          </div>
        ) : null}
        {error ? <div className="error-text">{error}</div> : null}
        <button className="btn btn-primary" type="submit" disabled={busy}>
          {busy ? "Sending…" : "Send reset link"}
        </button>
        <Link to="/login" className="small">
          Back to sign in
        </Link>
      </form>
    </div>
  );
}
