import { useEffect, useState, type FormEvent } from "react";
import { useSearchParams } from "react-router-dom";

import { api, formatBytes } from "../api";
import { useAuth } from "../auth";
import type { SessionInfo } from "../types";

export function SettingsPage() {
  const { auth, refresh } = useAuth();
  const [params] = useSearchParams();
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadSessions = () => {
    api.auth
      .sessions()
      .then((response) => setSessions(response.sessions))
      .catch((err: Error) => setError(err.message));
  };

  useEffect(() => {
    loadSessions();
  }, []);

  useEffect(() => {
    if (params.get("google") === "connected") setMessage("Google account connected.");
    if (params.get("google") === "error") setError("Google connection failed. Try again.");
  }, [params]);

  const changePassword = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setMessage(null);
    if (next.length < 10) {
      setError("Password must be at least 10 characters");
      return;
    }
    if (next !== confirm) {
      setError("Passwords do not match");
      return;
    }
    setBusy(true);
    try {
      await api.auth.changePassword(current, next);
      setCurrent("");
      setNext("");
      setConfirm("");
      setMessage("Password changed.");
      loadSessions();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not change password");
    } finally {
      setBusy(false);
    }
  };

  const connectGoogle = async () => {
    try {
      const response = await api.oauth.start();
      window.location.href = response.url;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Google OAuth is not configured");
    }
  };

  const disconnectGoogle = async () => {
    if (!window.confirm("Disconnect Google? Automated uploads will stop working.")) return;
    try {
      await api.oauth.disconnect();
      await refresh();
      setMessage("Google disconnected.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not disconnect");
    }
  };

  if (!auth) return null;

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Settings</h1>
          <p>Account, Google connection, storage, and sessions.</p>
        </div>
      </div>

      {message ? <div className="success-text" style={{ marginBottom: 12 }}>{message}</div> : null}
      {error ? <div className="error-text" style={{ marginBottom: 12 }}>{error}</div> : null}

      <div className="project-layout">
        <div className="project-main">
          <div className="card">
            <div className="card-title">Account</div>
            <div className="stack">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className="muted">Email</span>
                <span>{auth.user.email}</span>
              </div>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className="muted">Role</span>
                <span className="badge">{auth.user.role}</span>
              </div>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className="muted">Storage</span>
                <span>
                  {formatBytes(auth.quota.used_bytes)} / {formatBytes(auth.quota.quota_bytes)}
                </span>
              </div>
              <div className="progress">
                <div
                  style={{
                    width: `${Math.min(100, Math.round((auth.quota.used_bytes / Math.max(1, auth.quota.quota_bytes)) * 100))}%`,
                  }}
                />
              </div>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className="muted">Automated uploads today</span>
                <span>
                  {auth.upload_quota.uploads_used} / {auth.upload_quota.uploads_limit} shared · your limit{" "}
                  {auth.upload_quota.user_uploads_limit}
                </span>
              </div>
            </div>
          </div>

          <form className="card" onSubmit={changePassword}>
            <div className="card-title">Change password</div>
            <div className="stack">
              <div className="field">
                <label>Current password</label>
                <input
                  className="input"
                  type="password"
                  autoComplete="current-password"
                  value={current}
                  onChange={(event) => setCurrent(event.target.value)}
                  required
                />
              </div>
              <div className="split">
                <div className="field">
                  <label>New password</label>
                  <input
                    className="input"
                    type="password"
                    autoComplete="new-password"
                    value={next}
                    onChange={(event) => setNext(event.target.value)}
                    required
                  />
                </div>
                <div className="field">
                  <label>Confirm new password</label>
                  <input
                    className="input"
                    type="password"
                    autoComplete="new-password"
                    value={confirm}
                    onChange={(event) => setConfirm(event.target.value)}
                    required
                  />
                </div>
              </div>
              <button className="btn btn-primary" type="submit" disabled={busy}>
                {busy ? "Saving…" : "Change password"}
              </button>
            </div>
          </form>

          <div className="card">
            <div className="card-title">Sessions</div>
            <table className="table">
              <thead>
                <tr>
                  <th>Created</th>
                  <th>Last seen</th>
                  <th>IP</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {sessions.map((session) => (
                  <tr key={session.id}>
                    <td>{new Date(session.created_at).toLocaleString()}</td>
                    <td>{new Date(session.last_seen_at).toLocaleString()}</td>
                    <td className="mono">{session.ip || "—"}</td>
                    <td>
                      {session.current ? (
                        <span className="badge">current</span>
                      ) : (
                        <button
                          className="btn btn-sm btn-danger"
                          onClick={() => {
                            void api.auth.deleteSession(session.id).then(loadSessions);
                          }}
                        >
                          Revoke
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <aside className="project-side">
          <div className="card">
            <div className="card-title">Google / YouTube</div>
            {auth.google.connected ? (
              <div className="stack">
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <span className="badge green">connected</span>
                  <span className="small muted">{auth.google.account_email || "Google account"}</span>
                </div>
                <p className="small muted">
                  Automated uploads use the shared YouTube API quota. Manual download + Studio upload always works.
                </p>
                <button className="btn btn-sm btn-danger" onClick={disconnectGoogle}>
                  Disconnect Google
                </button>
              </div>
            ) : (
              <div className="stack">
                <p className="small muted">
                  Connect your own Google account to upload clips to your channel. The app requests only the
                  upload scope.
                </p>
                <button className="btn btn-primary btn-sm" onClick={connectGoogle}>
                  Connect Google
                </button>
              </div>
            )}
          </div>

          <div className="card">
            <div className="card-title">About this instance</div>
            <div className="stack small muted">
              <div>Invite-only, private projects.</div>
              <div>Max upload: 4 GB · Max source: 2 hours · Storage: 15 GB.</div>
              <div>Only upload content you own or have permission to use.</div>
              <div className="row">
                <a href="/privacy" target="_blank" rel="noreferrer">
                  Privacy
                </a>
                <a href="/terms" target="_blank" rel="noreferrer">
                  Terms
                </a>
              </div>
            </div>
          </div>
        </aside>
      </div>
    </>
  );
}
