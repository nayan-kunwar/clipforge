import { useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { useAuth } from "../auth";

export function InvitePage() {
  const { token = "" } = useParams();
  const { acceptInvite } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    if (password.length < 10) {
      setError("Password must be at least 10 characters");
      return;
    }
    if (password !== confirm) {
      setError("Passwords do not match");
      return;
    }
    setBusy(true);
    try {
      await acceptInvite(token, password, email || undefined);
      navigate("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invitation could not be accepted");
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
          <h1>Accept invitation</h1>
          <p className="muted small" style={{ marginTop: 6 }}>
            Choose a password for your new account.
          </p>
        </div>
        <div className="field">
          <label>Email (only for link-only invites)</label>
          <input
            className="input"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            placeholder="you@example.com"
          />
        </div>
        <div className="field">
          <label>Password</label>
          <input
            className="input"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
        </div>
        <div className="field">
          <label>Confirm password</label>
          <input
            className="input"
            type="password"
            autoComplete="new-password"
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
            required
          />
        </div>
        {error ? <div className="error-text">{error}</div> : null}
        <button className="btn btn-primary" type="submit" disabled={busy}>
          {busy ? "Creating account…" : "Create account"}
        </button>
      </form>
    </div>
  );
}
