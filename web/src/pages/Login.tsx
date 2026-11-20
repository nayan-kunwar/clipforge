import { useState, type FormEvent } from "react";
import { Link, Navigate, useNavigate } from "react-router-dom";

import { useAuth } from "../auth";

export function LoginPage() {
  const { auth, login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (auth) return <Navigate to="/" replace />;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      navigate("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign in failed");
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
          <h1>Sign in</h1>
          <p className="muted small" style={{ marginTop: 6 }}>
            Invite-only access to your clip workspace.
          </p>
        </div>
        <div className="field">
          <label>Email</label>
          <input
            className="input"
            type="email"
            autoComplete="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
          />
        </div>
        <div className="field">
          <label>Password</label>
          <input
            className="input"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
        </div>
        {error ? <div className="error-text">{error}</div> : null}
        <button className="btn btn-primary" type="submit" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <div className="row small" style={{ justifyContent: "space-between" }}>
          <Link to="/forgot-password">Forgot password?</Link>
          <span className="faint">Invite required</span>
        </div>
      </form>
    </div>
  );
}
