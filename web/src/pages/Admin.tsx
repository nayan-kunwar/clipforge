import { useEffect, useState, type FormEvent } from "react";

import { api, copyText, formatBytes, formatDuration } from "../api";
import type { AdminJob, AdminSettings, AdminUser, Invite, Stats } from "../types";

type Tab = "users" | "invites" | "jobs" | "usage" | "errors" | "settings";

export function AdminPage() {
  const [tab, setTab] = useState<Tab>("users");
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [jobs, setJobs] = useState<AdminJob[]>([]);
  const [stats, setStats] = useState<Stats | null>(null);
  const [errors, setErrors] = useState<Array<Record<string, unknown>>>([]);
  const [limits, setLimits] = useState<AdminSettings | null>(null);
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteUrl, setInviteUrl] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [jobFilter, setJobFilter] = useState("");
  const [loading, setLoading] = useState(false);

  const notify = (text: string) => {
    setMessage(text);
    window.setTimeout(() => setMessage((current) => (current === text ? null : current)), 3500);
  };

  const load = async (target: Tab = tab) => {
    setLoading(true);
    setError(null);
    try {
      if (target === "users") setUsers((await api.admin.users()).users);
      if (target === "invites") setInvites((await api.admin.invites()).invites);
      if (target === "jobs") setJobs((await api.admin.jobs(jobFilter || undefined)).jobs);
      if (target === "usage") setStats(await api.admin.stats());
      if (target === "errors") setErrors((await api.admin.errors()).errors);
      if (target === "settings") setLimits((await api.admin.settings()).settings);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Admin request failed");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load(tab);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, jobFilter]);

  const createInvite = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setInviteUrl(null);
    try {
      const response = await api.admin.createInvite(inviteEmail || undefined);
      setInviteUrl(response.invite_url);
      setInviteEmail("");
      notify(response.email.status === "sent" ? "Invitation emailed" : "Invite created");
      await load("invites");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create invite");
    }
  };

  const copy = async (value: string) => {
    notify((await copyText(value)) ? "Copied" : "Copy failed");
  };

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Admin</h1>
          <p>Account metadata, jobs, usage, and sanitized errors. Private media is not visible here.</p>
        </div>
        <button className="btn" onClick={() => load(tab)} disabled={loading}>
          Refresh
        </button>
      </div>

      {message ? <div className="success-text" style={{ marginBottom: 12 }}>{message}</div> : null}
      {error ? <div className="error-text" style={{ marginBottom: 12 }}>{error}</div> : null}

      <div className="tabs">
        {(["users", "invites", "jobs", "usage", "errors", "settings"] as Tab[]).map((value) => (
          <button key={value} className={tab === value ? "active" : ""} onClick={() => setTab(value)}>
            {value}
          </button>
        ))}
      </div>

      {tab === "users" ? (
        <div className="card">
          <table className="table">
            <thead>
              <tr>
                <th>Email</th>
                <th>Role</th>
                <th>Status</th>
                <th>Storage</th>
                <th>Projects</th>
                <th>Google</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {users.map((user) => (
                <tr key={user.id}>
                  <td>
                    <div>{user.email}</div>
                    <div className="small faint">
                      joined {new Date(user.created_at).toLocaleDateString()}
                    </div>
                  </td>
                  <td>
                    <span className="badge">{user.role}</span>
                  </td>
                  <td>
                    <span className={`badge ${user.is_active ? "green" : "red"}`}>
                      {user.is_active ? "active" : "disabled"}
                    </span>
                  </td>
                  <td>
                    {formatBytes(user.storage_bytes)}
                    <div className="small faint">of {formatBytes(user.quota_bytes)}</div>
                  </td>
                  <td>{user.project_count}</td>
                  <td>{user.google_connected ? "connected" : "—"}</td>
                  <td>
                    <div className="row">
                      <button
                        className="btn btn-sm"
                        onClick={async () => {
                          await api.admin.patchUser(user.id, { is_active: !user.is_active });
                          await load("users");
                        }}
                      >
                        {user.is_active ? "Disable" : "Enable"}
                      </button>
                      <button
                        className="btn btn-sm"
                        onClick={async () => {
                          const response = await api.admin.resetPassword(user.id);
                          if (response.reset_url) {
                            await copy(response.reset_url);
                            notify("Reset link copied (email not configured)");
                          } else {
                            notify("Reset email sent");
                          }
                        }}
                      >
                        Reset password
                      </button>
                      <button
                        className="btn btn-sm"
                        onClick={async () => {
                          await api.admin.revokeSessions(user.id);
                          notify("Sessions revoked");
                        }}
                      >
                        Revoke sessions
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}

      {tab === "invites" ? (
        <div className="stack">
          <form className="card" onSubmit={createInvite}>
            <div className="card-title">Invite a user</div>
            <div className="row">
              <input
                className="input"
                style={{ maxWidth: 320 }}
                type="email"
                placeholder="email@example.com (optional)"
                value={inviteEmail}
                onChange={(event) => setInviteEmail(event.target.value)}
              />
              <button className="btn btn-primary" type="submit">
                Create invite
              </button>
            </div>
            {inviteUrl ? (
              <div className="row small" style={{ marginTop: 10 }}>
                <code style={{ overflowWrap: "anywhere" }}>{inviteUrl}</code>
                <button className="btn btn-sm" type="button" onClick={() => copy(inviteUrl)}>
                  Copy link
                </button>
              </div>
            ) : null}
            <div className="hint" style={{ marginTop: 8 }}>
              Email is optional; when Brevo is configured, an invitation is sent automatically.
            </div>
          </form>
          <div className="card">
            <table className="table">
              <thead>
                <tr>
                  <th>Email</th>
                  <th>State</th>
                  <th>Created</th>
                  <th>Expires</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {invites.map((invite) => (
                  <tr key={invite.id}>
                    <td>{invite.email || "link-only"}</td>
                    <td>
                      <span className={`badge ${invite.state === "pending" ? "yellow" : invite.state === "accepted" ? "green" : "red"}`}>
                        {invite.state}
                      </span>
                    </td>
                    <td>{new Date(invite.created_at).toLocaleString()}</td>
                    <td>{new Date(invite.expires_at).toLocaleString()}</td>
                    <td>
                      <div className="row">
                        {invite.state === "pending" ? (
                          <>
                            <button
                              className="btn btn-sm"
                              onClick={async () => {
                                const response = await api.admin.resendInvite(invite.id);
                                setInviteUrl(response.invite_url);
                                notify("Invite link refreshed");
                                await load("invites");
                              }}
                            >
                              Refresh link
                            </button>
                            <button
                              className="btn btn-sm btn-danger"
                              onClick={async () => {
                                await api.admin.revokeInvite(invite.id);
                                await load("invites");
                              }}
                            >
                              Revoke
                            </button>
                          </>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {tab === "jobs" ? (
        <div className="stack">
          <div className="row">
            <select className="select" style={{ maxWidth: 180 }} value={jobFilter} onChange={(event) => setJobFilter(event.target.value)}>
              <option value="">All statuses</option>
              <option value="queued">queued</option>
              <option value="running">running</option>
              <option value="succeeded">succeeded</option>
              <option value="failed">failed</option>
              <option value="cancelled">cancelled</option>
            </select>
          </div>
          <div className="card">
            <table className="table">
              <thead>
                <tr>
                  <th>User</th>
                  <th>Kind</th>
                  <th>Status</th>
                  <th>Progress</th>
                  <th>Created</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
                  <tr key={job.id}>
                    <td>{job.user_email}</td>
                    <td>{job.kind}</td>
                    <td>
                      <span className={`badge ${job.status === "failed" ? "red" : job.status === "succeeded" ? "green" : "yellow"}`}>
                        {job.status}
                      </span>
                      {job.error_code ? <div className="small faint">{job.error_code}</div> : null}
                    </td>
                    <td>{Math.round((job.progress || 0) * 100)}%</td>
                    <td>{new Date(job.created_at).toLocaleString()}</td>
                    <td>
                      <div className="row">
                        {job.status === "queued" || job.status === "running" ? (
                          <button
                            className="btn btn-sm btn-danger"
                            onClick={async () => {
                              await api.admin.cancelJob(job.id);
                              await load("jobs");
                            }}
                          >
                            Cancel
                          </button>
                        ) : null}
                        {job.status === "failed" || job.status === "cancelled" ? (
                          <button
                            className="btn btn-sm"
                            onClick={async () => {
                              await api.admin.retryJob(job.id);
                              await load("jobs");
                            }}
                          >
                            Retry
                          </button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {tab === "usage" && stats ? (
        <div className="stack">
          <div className="admin-grid">
            <div className="stat">
              <div className="stat-label">Users</div>
              <div className="stat-value">{stats.users}</div>
            </div>
            <div className="stat">
              <div className="stat-label">Projects</div>
              <div className="stat-value">{stats.projects}</div>
            </div>
            <div className="stat">
              <div className="stat-label">Jobs queued</div>
              <div className="stat-value">{stats.jobs_queued}</div>
            </div>
            <div className="stat">
              <div className="stat-label">Jobs running</div>
              <div className="stat-value">{stats.jobs_running}</div>
            </div>
            <div className="stat">
              <div className="stat-label">Jobs failed</div>
              <div className="stat-value">{stats.jobs_failed}</div>
            </div>
            <div className="stat">
              <div className="stat-label">Disk free</div>
              <div className="stat-value">{formatBytes(stats.disk_free_bytes)}</div>
            </div>
          </div>
          <div className="card">
            <div className="card-title">YouTube upload quota (shared)</div>
            <div className="split">
              <div>
                <div className="small muted">Uploads today</div>
                <div style={{ fontSize: 24, fontWeight: 800 }}>
                  {stats.quota.uploads_used} / {stats.quota.uploads_limit}
                </div>
              </div>
              <div>
                <div className="small muted">API units</div>
                <div style={{ fontSize: 24, fontWeight: 800 }}>
                  {stats.quota.units_used} / {stats.quota.units_limit}
                </div>
              </div>
            </div>
            <div className="hint" style={{ marginTop: 8 }}>
              Quota resets at midnight Pacific. Manual Studio uploads do not consume API quota.
            </div>
          </div>
        </div>
      ) : null}

      {tab === "errors" ? (
        <div className="card">
          {errors.length === 0 ? <div className="hint">No failed jobs.</div> : null}
          <div className="stack">
            {errors.map((item, index) => (
              <div key={index} className="candidate-item">
                <div className="candidate-head">
                  <span className="candidate-title">{String(item.user_email)}</span>
                  <span className="badge red">{String(item.kind)}</span>
                </div>
                <div className="small faint">{String(item.finished_at || "")}</div>
                <div className="candidate-why">{String(item.error || "")}</div>
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {tab === "settings" && limits ? (
        <form
          className="card"
          onSubmit={async (event) => {
            event.preventDefault();
            try {
              await api.admin.patchSettings({
                max_upload_bytes: limits.max_upload_bytes,
                max_source_seconds: limits.max_source_seconds,
                user_storage_bytes: limits.user_storage_bytes,
                upload_daily_global: limits.upload_daily_global,
                upload_daily_per_user: limits.upload_daily_per_user,
                default_skip_transcript: limits.default_skip_transcript,
              });
              notify("Settings saved");
            } catch (err) {
              setError(err instanceof Error ? err.message : "Could not save settings");
            }
          }}
        >
          <div className="card-title">Limits</div>
          <div className="split">
            <div className="field">
              <label>Max upload (bytes)</label>
              <input
                className="input"
                type="number"
                value={limits.max_upload_bytes}
                onChange={(event) => setLimits({ ...limits, max_upload_bytes: Number(event.target.value) })}
              />
            </div>
            <div className="field">
              <label>Max source seconds</label>
              <input
                className="input"
                type="number"
                value={limits.max_source_seconds}
                onChange={(event) => setLimits({ ...limits, max_source_seconds: Number(event.target.value) })}
              />
            </div>
            <div className="field">
              <label>Storage per user (bytes)</label>
              <input
                className="input"
                type="number"
                value={limits.user_storage_bytes}
                onChange={(event) => setLimits({ ...limits, user_storage_bytes: Number(event.target.value) })}
              />
            </div>
            <div className="field">
              <label>Shared uploads/day</label>
              <input
                className="input"
                type="number"
                value={limits.upload_daily_global}
                onChange={(event) => setLimits({ ...limits, upload_daily_global: Number(event.target.value) })}
              />
            </div>
            <div className="field">
              <label>Uploads/user/day</label>
              <input
                className="input"
                type="number"
                value={limits.upload_daily_per_user}
                onChange={(event) => setLimits({ ...limits, upload_daily_per_user: Number(event.target.value) })}
              />
            </div>
            <div className="field">
              <label>Max invited users</label>
              <input className="input" value={limits.max_invited_users} disabled />
            </div>
          </div>
          <div className="row" style={{ marginTop: 12 }}>
            <label className="row small">
              <input
                type="checkbox"
                checked={limits.default_skip_transcript}
                onChange={(event) => setLimits({ ...limits, default_skip_transcript: event.target.checked })}
              />
              Skip transcript by default
            </label>
          </div>
          <button className="btn btn-primary" type="submit" style={{ marginTop: 12 }}>
            Save settings
          </button>
        </form>
      ) : null}

      {loading ? <div className="hint" style={{ marginTop: 12 }}>Loading…</div> : null}
    </>
  );
}
