import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { api } from "../api";

export function ResetPasswordPage() {
  const [params] = useSearchParams();
  const token = params.get("token") || "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    if (!token) {
      setError("This reset link is missing its token.");
      return;
    }
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
      await api.auth.resetPassword(token, password);
      setMessage("Password reset. You can now sign in.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Reset failed");
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
          <h1>Choose a new password</h1>
          <p className="muted small" style={{ marginTop: 6 }}>
            The link is valid for one hour and can be used once.
          </p>
        </div>
        <div className="field">
          <label>New password</label>
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
        {message ? <div className="success-text">{message}</div> : null}
        {error ? <div className="error-text">{error}</div> : null}
        <button className="btn btn-primary" type="submit" disabled={busy}>
          {busy ? "Resetting…" : "Reset password"}
        </button>
        <Link to="/login" className="small">
          Back to sign in
        </Link>
      </form>
    </div>
  );
}
