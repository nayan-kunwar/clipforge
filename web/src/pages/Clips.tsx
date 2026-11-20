import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api, copyText, formatBytes, formatDuration } from "../api";
import { useAuth } from "../auth";
import { JobPanel } from "../components/JobPanel";
import type { Clip, Project } from "../types";

export function ClipsPage() {
  const { id = "" } = useParams();
  const { auth } = useAuth();
  const [project, setProject] = useState<Project | null>(null);
  const [clips, setClips] = useState<Clip[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [privacy, setPrivacy] = useState("private");
  const [form, setForm] = useState({
    overlay: "",
    suggested_title: "",
    suggested_description: "",
    tags: "",
  });

  const selected = useMemo(() => clips.find((clip) => clip.id === selectedId) || clips[0] || null, [clips, selectedId]);

  const load = useCallback(async () => {
    try {
      const [nextProject, nextClips] = await Promise.all([api.projects.get(id), api.projects.clips(id)]);
      setProject(nextProject);
      setClips(nextClips.clips);
      setSelectedId((current) => current ?? nextClips.clips[0]?.id ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load clips");
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!selected) return;
    setForm({
      overlay: selected.overlay || "",
      suggested_title: selected.suggested_title || "",
      suggested_description: selected.suggested_description || "",
      tags: (selected.tags || []).join(", "),
    });
  }, [selected]);

  const notify = (message: string) => {
    setNotice(message);
    window.setTimeout(() => setNotice((current) => (current === message ? null : current)), 3000);
  };

  const handleJobDone = useCallback(() => {
    setActiveJobId(null);
    void load();
  }, [load]);

  const handleJobError = useCallback((message: string) => setError(message), []);

  const save = async () => {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      await api.clips.patch(id, selected.id, {
        overlay: form.overlay,
        suggested_title: form.suggested_title,
        suggested_description: form.suggested_description,
        tags: form.tags
          .split(",")
          .map((tag) => tag.trim())
          .filter(Boolean),
      });
      notify("Clip metadata saved");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save clip");
    } finally {
      setBusy(false);
    }
  };

  const rerender = async () => {
    if (!selected) return;
    try {
      const response = await api.clips.rerender(id, selected.id);
      setActiveJobId(response.job.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start re-render");
    }
  };

  const upload = async () => {
    if (!selected) return;
    setBusy(true);
    setError(null);
    try {
      const response = await api.clips.upload(id, [selected.id], privacy);
      if (response.jobs[0]) setActiveJobId(response.jobs[0].id);
      notify("Upload queued");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not queue upload");
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

  const markManual = async () => {
    if (!selected) return;
    try {
      await api.clips.markManual(id, selected.id);
      notify("Marked as manually published");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update clip");
    }
  };

  const copy = async (value: string, label: string) => {
    const ok = await copyText(value);
    notify(ok ? `${label} copied` : "Copy failed");
  };

  if (!project) return <div className="empty">{error || "Loading clips…"}</div>;

  return (
    <>
      <div className="page-header">
        <div>
          <div className="row small muted" style={{ marginBottom: 6 }}>
            <Link to="/">Library</Link>
            <span>/</span>
            <Link to={`/p/${id}`}>{project.title}</Link>
            <span>/</span>
            <span>Clips</span>
          </div>
          <h1>Rendered clips</h1>
          <p>
            {clips.length} clip{clips.length === 1 ? "" : "s"} · {formatBytes(project.storage_bytes)} used
          </p>
        </div>
        <div className="row">
          <a className="btn" href={api.projects.manifestUrl(id)}>
            Download manifest
          </a>
          <Link className="btn btn-primary" to={`/p/${id}`}>
            Back to timeline
          </Link>
        </div>
      </div>

      {error ? <div className="error-text" style={{ marginBottom: 12 }}>{error}</div> : null}
      {notice ? <div className="toast">{notice}</div> : null}
      <JobPanel jobId={activeJobId} onDone={handleJobDone} onError={handleJobError} />

      {clips.length === 0 ? (
        <div className="empty" style={{ marginTop: 16 }}>
          No rendered clips yet.
          <div style={{ marginTop: 12 }}>
            <Link className="btn btn-primary" to={`/p/${id}`}>
              Select moments and render
            </Link>
          </div>
        </div>
      ) : (
        <div className="project-layout" style={{ marginTop: 16 }}>
          <div className="clip-grid">
            {clips.map((clip) => (
              <article
                className="clip-card"
                key={clip.id}
                onClick={() => setSelectedId(clip.id)}
                style={{ outline: selected?.id === clip.id ? "2px solid var(--primary)" : "none" }}
              >
                <img className="clip-video" src={clip.thumb_url} alt={clip.overlay} />
                <div className="clip-body">
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <span className={`badge ${clip.ok ? "green" : "red"}`}>{clip.ok ? "ok" : "error"}</span>
                    {clip.stale ? <span className="badge yellow">stale</span> : null}
                    {clip.youtube_url ? <span className="badge blue">youtube</span> : null}
                  </div>
                  <div style={{ fontWeight: 700, fontSize: 13 }}>{clip.overlay}</div>
                  <div className="small faint">
                    {formatDuration(clip.length_s)} · from {clip.src_start}
                  </div>
                </div>
              </article>
            ))}
          </div>

          {selected ? (
            <aside className="project-side">
              <div className="card">
                <div className="card-title">Preview</div>
                <video className="video-preview" src={selected.file_url} controls preload="metadata" />
                <div className="row" style={{ marginTop: 10 }}>
                  <a className="btn btn-sm" href={selected.download_url}>
                    Download MP4
                  </a>
                  <button className="btn btn-sm" onClick={rerender} disabled={busy}>
                    Re-render
                  </button>
                </div>
                {selected.youtube_url ? (
                  <div className="small" style={{ marginTop: 8 }}>
                    Uploaded: <a href={selected.youtube_url}>{selected.youtube_url}</a>
                  </div>
                ) : null}
                {selected.manual_published_at ? (
                  <div className="small faint" style={{ marginTop: 8 }}>
                    Marked as manually published
                  </div>
                ) : null}
              </div>

              <div className="card">
                <div className="card-title">Metadata</div>
                <div className="stack">
                  <div className="field">
                    <label>Overlay title (requires re-render)</label>
                    <input
                      className="input"
                      value={form.overlay}
                      onChange={(event) => setForm({ ...form, overlay: event.target.value })}
                    />
                  </div>
                  <div className="field">
                    <label>YouTube title</label>
                    <input
                      className="input"
                      maxLength={100}
                      value={form.suggested_title}
                      onChange={(event) => setForm({ ...form, suggested_title: event.target.value })}
                    />
                  </div>
                  <div className="field">
                    <label>Description</label>
                    <textarea
                      className="textarea"
                      value={form.suggested_description}
                      onChange={(event) => setForm({ ...form, suggested_description: event.target.value })}
                    />
                  </div>
                  <div className="field">
                    <label>Tags (comma separated)</label>
                    <input
                      className="input"
                      value={form.tags}
                      onChange={(event) => setForm({ ...form, tags: event.target.value })}
                    />
                  </div>
                  <div className="row">
                    <button className="btn btn-primary btn-sm" onClick={save} disabled={busy}>
                      {busy ? "Saving…" : "Save metadata"}
                    </button>
                    <button
                      className="btn btn-sm"
                      onClick={() => copy(form.suggested_title, "Title")}
                    >
                      Copy title
                    </button>
                    <button
                      className="btn btn-sm"
                      onClick={() => copy(form.suggested_description, "Description")}
                    >
                      Copy description
                    </button>
                  </div>
                </div>
              </div>

              <div className="card">
                <div className="card-title">Publish</div>
                {auth?.google.connected ? (
                  <>
                    <div className="small muted" style={{ marginBottom: 8 }}>
                      Connected as {auth.google.account_email || "Google account"}
                    </div>
                    <div className="row">
                      <select className="select" style={{ width: 140 }} value={privacy} onChange={(event) => setPrivacy(event.target.value)}>
                        <option value="private">Private</option>
                        <option value="unlisted">Unlisted</option>
                        <option value="public">Public</option>
                      </select>
                      <button className="btn btn-primary btn-sm" onClick={upload} disabled={busy}>
                        Upload clip
                      </button>
                    </div>
                    <div className="hint" style={{ marginTop: 6 }}>
                      {auth?.upload_quota
                        ? `${auth.upload_quota.uploads_used}/${auth.upload_quota.uploads_limit} shared automated uploads used today.`
                        : "Uploads consume the shared daily YouTube API quota."}
                    </div>
                  </>
                ) : (
                  <div className="stack">
                    <div className="small muted">Google is not connected.</div>
                    <button className="btn btn-sm" onClick={connectGoogle}>
                      Connect Google
                    </button>
                  </div>
                )}
                <hr style={{ border: 0, borderTop: "1px solid var(--border)", margin: "12px 0" }} />
                <div className="small muted" style={{ marginBottom: 8 }}>
                  Manual fallback: download the MP4, copy the metadata, upload in YouTube Studio.
                </div>
                <div className="row">
                  <a className="btn btn-sm" href={selected.download_url}>
                    Download
                  </a>
                  <button
                    className="btn btn-sm"
                    onClick={() => window.open("https://studio.youtube.com", "_blank", "noopener")}
                  >
                    Open YouTube Studio
                  </button>
                  <button className="btn btn-sm btn-ghost" onClick={markManual}>
                    Mark manual
                  </button>
                </div>
              </div>
            </aside>
          ) : null}
        </div>
      )}
    </>
  );
}
