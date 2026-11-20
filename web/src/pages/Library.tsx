import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api, formatBytes, formatDuration } from "../api";
import type { Project } from "../types";

function statusBadge(status: string): string {
  if (["ready", "analyzed", "rendered"].includes(status)) return "green";
  if (["failed", "cancelled"].includes(status)) return "red";
  if (["ingesting", "analyzing", "rendering", "queued"].includes(status)) return "yellow";
  return "blue";
}

export function LibraryPage() {
  const navigate = useNavigate();
  const [projects, setProjects] = useState<Project[]>([]);
  const [storage, setStorage] = useState({ used_bytes: 0, quota_bytes: 1 });
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    api.projects
      .list()
      .then((response) => {
        setProjects(response.projects);
        setStorage(response.storage);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
  }, []);

  const remove = (project: Project) => {
    if (!window.confirm(`Delete "${project.title}" and all of its media?`)) return;
    api.projects
      .delete(project.id)
      .then(load)
      .catch((err: Error) => setError(err.message));
  };

  const percent = Math.min(100, Math.round((storage.used_bytes / Math.max(1, storage.quota_bytes)) * 100));

  return (
    <>
      <div className="page-header">
        <div>
          <h1>Library</h1>
          <p>Your private projects. Only you can see the media.</p>
        </div>
        <Link className="btn btn-primary" to="/new">
          New project
        </Link>
      </div>

      <div className="card" style={{ marginBottom: 18 }}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <span className="small muted">Storage</span>
          <span className="small">
            {formatBytes(storage.used_bytes)} / {formatBytes(storage.quota_bytes)}
          </span>
        </div>
        <div className="progress quota-bar">
          <div style={{ width: `${percent}%` }} />
        </div>
      </div>

      {error ? <div className="error-text" style={{ marginBottom: 12 }}>{error}</div> : null}
      {loading ? <div className="empty">Loading projects…</div> : null}
      {!loading && projects.length === 0 ? (
        <div className="empty">
          <div style={{ fontSize: 18, fontWeight: 700, color: "var(--text)", marginBottom: 6 }}>
            No projects yet
          </div>
          Start with a YouTube URL or upload a local video.
          <div style={{ marginTop: 16 }}>
            <Link className="btn btn-primary" to="/new">
              Create your first project
            </Link>
          </div>
        </div>
      ) : null}

      <div className="project-grid">
        {projects.map((project) => (
          <article
            className="project-card"
            key={project.id}
            onClick={() => navigate(`/p/${project.id}`)}
            role="button"
            tabIndex={0}
            onKeyDown={(event) => {
              if (event.key === "Enter") navigate(`/p/${project.id}`);
            }}
          >
            <div className="project-poster">
              {project.poster_url ? <img src={project.poster_url} alt="" /> : <span>No preview yet</span>}
            </div>
            <div className="project-body">
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className={`badge ${statusBadge(project.status)}`}>{project.status}</span>
                <span className="small faint">{formatDuration(project.duration_s)}</span>
              </div>
              <div className="project-title">{project.title}</div>
              <div className="project-meta">
                <span>{project.source_type === "youtube" ? "YouTube" : "Upload"}</span>
                <span>
                  {project.moment_count} {project.moment_count === 1 ? "moment" : "moments"} ·{" "}
                  {project.clip_count} {project.clip_count === 1 ? "clip" : "clips"}
                </span>
              </div>
              <div className="project-meta">
                <span>{formatBytes(project.storage_bytes)}</span>
                <button
                  className="btn btn-sm btn-danger"
                  onClick={(event) => {
                    event.stopPropagation();
                    remove(project);
                  }}
                >
                  Delete
                </button>
              </div>
            </div>
          </article>
        ))}
      </div>
    </>
  );
}
