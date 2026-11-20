import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api, formatBytes, formatDuration } from "../api";
import { JobPanel } from "../components/JobPanel";
import { Timeline } from "../components/Timeline";
import type { Candidate, Job, Moment, Project, Timeline as TimelineData } from "../types";

function statusClass(status: string): string {
  if (["ready", "analyzed", "rendered"].includes(status)) return "green";
  if (["failed", "cancelled"].includes(status)) return "red";
  if (["ingesting", "analyzing", "rendering", "queued"].includes(status)) return "yellow";
  return "blue";
}

function newMomentId(): string {
  return `m_${crypto.randomUUID().replace(/-/g, "").slice(0, 10)}`;
}

function fromCandidate(candidate: Candidate): Moment {
  return {
    id: newMomentId(),
    start: candidate.start,
    end: candidate.end,
    peak: candidate.peak ?? candidate.start,
    title: candidate.title || "CLIP",
    source: candidate.source,
    score: candidate.score || 0,
    why: candidate.why || "",
    enabled: true,
  };
}

export function ProjectPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [project, setProject] = useState<Project | null>(null);
  const [timeline, setTimeline] = useState<TimelineData | null>(null);
  const [moments, setMoments] = useState<Moment[]>([]);
  const [currentTime, setCurrentTime] = useState(0);
  const [activeJobId, setActiveJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [sources, setSources] = useState<Record<string, boolean>>({});
  const [showCandidates, setShowCandidates] = useState(true);

  const load = useCallback(async () => {
    try {
      const [nextProject, nextTimeline] = await Promise.all([
        api.projects.get(id),
        api.projects.timeline(id),
      ]);
      setProject(nextProject);
      setTimeline(nextTimeline);
      setMoments(nextTimeline.moments || []);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load project");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    setLoading(true);
    void load();
  }, [load]);

  useEffect(() => {
    if (!project) return;
    if (!["ingesting", "analyzing", "rendering", "queued"].includes(project.status)) return;
    const handle = window.setInterval(() => {
      api.projects
        .get(id)
        .then((next) => {
          setProject(next);
          if (!["ingesting", "analyzing", "rendering", "queued"].includes(next.status)) {
            void load();
          }
        })
        .catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(handle);
  }, [id, project, load]);

  const notify = (message: string) => {
    setNotice(message);
    window.setTimeout(() => setNotice((current) => (current === message ? null : current)), 3500);
  };

  const handleJobDone = useCallback(() => {
    setActiveJobId(null);
    void load();
  }, [load]);

  const handleJobError = useCallback((message: string) => {
    setError(message);
  }, []);

  const seek = (time: number) => {
    const clamped = Math.max(0, Math.min(project?.duration_s || 0, time));
    setCurrentTime(clamped);
    if (videoRef.current) {
      videoRef.current.currentTime = clamped;
    }
  };

  const addAtPlayhead = () => {
    const duration = project?.duration_s || 0;
    if (duration <= 0) return;
    let start = currentTime;
    if (duration - start < 1) start = Math.max(0, duration - 1);
    const end = Math.min(duration, start + Math.min(30, Math.max(1, duration - start)));
    const moment: Moment = {
      id: newMomentId(),
      start,
      end,
      peak: start,
      title: "CLIP",
      source: "user",
      score: 0,
      why: "Manual selection",
      enabled: true,
    };
    setMoments((previous) => [...previous, moment]);
    notify("Clip added at the playhead");
  };

  const addCandidate = (candidate: Candidate) => {
    const moment = fromCandidate(candidate);
    setMoments((previous) => {
      if (previous.some((item) => Math.abs(item.start - moment.start) < 0.25 && item.source === moment.source)) {
        return previous;
      }
      return [...previous, moment];
    });
  };

  const updateMoment = (index: number, patch: Partial<Moment>) => {
    const duration = project?.duration_s || 0;
    setMoments((previous) =>
      previous.map((moment, itemIndex) => {
        if (itemIndex !== index) return moment;
        const next = { ...moment, ...patch };
        next.start = Math.max(0, Math.min(next.start, Math.max(0, duration - 1)));
        next.end = Math.max(next.start + 1, Math.min(next.end, duration, next.start + 60));
        if (next.end - next.start > 60) next.end = next.start + 60;
        return next;
      }),
    );
  };

  const saveMoments = async () => {
    setSaving(true);
    setError(null);
    try {
      const response = await api.projects.patchMoments(id, moments);
      setMoments(response.moments);
      notify("Moments saved");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save moments");
    } finally {
      setSaving(false);
    }
  };

  const runAnalyze = async () => {
    try {
      const response = await api.projects.analyze(id);
      setActiveJobId(response.job.id);
      setProject((previous) => (previous ? { ...previous, status: "analyzing" } : previous));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start analysis");
    }
  };

  const runRender = async () => {
    setError(null);
    try {
      await api.projects.patchMoments(id, moments);
      const enabled = moments.filter((moment) => moment.enabled).map((moment) => moment.id);
      if (enabled.length === 0) throw new Error("Enable at least one moment first");
      const response = await api.projects.render(id, enabled);
      setActiveJobId(response.job.id);
      setProject((previous) => (previous ? { ...previous, status: "rendering" } : previous));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start render");
    }
  };

  const candidates = useMemo(() => {
    if (!timeline) return [];
    return timeline.candidates.filter((candidate) => sources[candidate.source] !== false);
  }, [timeline, sources]);

  if (loading) return <div className="empty">Loading project…</div>;
  if (!project) return <div className="empty">{error || "Project not found"}</div>;

  const duration = project.duration_s;
  const readyForTimeline = duration > 0;

  return (
    <>
      <div className="page-header">
        <div>
          <div className="row small muted" style={{ marginBottom: 6 }}>
            <Link to="/">Library</Link>
            <span>/</span>
            <span>{project.source_type === "youtube" ? "YouTube" : "Upload"}</span>
          </div>
          <h1>{project.title}</h1>
          <p>
            {formatDuration(duration)} · {moments.length} {moments.length === 1 ? "moment" : "moments"} ·{" "}
            {project.clip_count} {project.clip_count === 1 ? "clip" : "clips"}
          </p>
        </div>
        <div className="row">
          <span className={`badge ${statusClass(project.status)}`}>{project.status}</span>
          <Link className="btn" to={`/p/${id}/clips`}>
            Clips
          </Link>
          {project.status === "ingesting" || project.status === "queued" ? null : (
            <button className="btn" onClick={runAnalyze} disabled={!!activeJobId}>
              {timeline?.analysis_available ? "Re-analyze" : "Analyze"}
            </button>
          )}
          <button className="btn btn-primary" onClick={runRender} disabled={!readyForTimeline || !!activeJobId}>
            Render clips
          </button>
          <button
            className="btn btn-ghost"
            title="Delete cached 1080p segments and transcript snippets"
            onClick={async () => {
              if (!window.confirm("Delete cached intermediates for this project? Renders stay.")) return;
              try {
                const response = await api.projects.cleanup(id);
                notify(`Freed ${formatBytes(response.freed_bytes)}`);
                await load();
              } catch (err) {
                setError(err instanceof Error ? err.message : "Cleanup failed");
              }
            }}
          >
            Clear cache
          </button>
        </div>
      </div>

      {error ? <div className="error-text" style={{ marginBottom: 12 }}>{error}</div> : null}
      {project.latest_job?.status === "failed" && project.latest_job.error_detail ? (
        <div className="error-text" style={{ marginBottom: 12 }}>
          Last job ({project.latest_job.kind}) failed: {project.latest_job.error_detail}
        </div>
      ) : null}
      {notice ? <div className="toast">{notice}</div> : null}

      <div className="project-layout">
        <div className="project-main">
          {project.preview_url ? (
            <video
              ref={videoRef}
              className="video-preview"
              src={project.preview_url}
              controls
              preload="metadata"
              onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
            />
          ) : (
            <div className="empty">Preview is being prepared…</div>
          )}

          {readyForTimeline && timeline ? (
            <Timeline
              duration={duration}
              peaks={timeline.waveform?.peaks || null}
              candidates={candidates}
              moments={moments}
              currentTime={currentTime}
              onChange={setMoments}
              onSeek={seek}
            />
          ) : (
            <div className="empty">Timeline will appear after ingest finishes.</div>
          )}

          <div className="row">
            <button className="btn btn-sm" onClick={addAtPlayhead} disabled={!readyForTimeline}>
              Add clip at playhead
            </button>
            <button className="btn btn-sm" onClick={saveMoments} disabled={saving || moments.length === 0}>
              {saving ? "Saving…" : "Save moments"}
            </button>
            <button
              className="btn btn-sm"
              onClick={() => {
                setShowCandidates((value) => !value);
              }}
            >
              {showCandidates ? "Hide candidates" : "Show candidates"}
            </button>
            <span className="hint">Playhead {formatDuration(currentTime)}</span>
          </div>

          <JobPanel jobId={activeJobId} onDone={handleJobDone} onError={handleJobError} />
        </div>

        <aside className="project-side">
          {showCandidates ? (
            <div className="card">
              <div className="row" style={{ justifyContent: "space-between", marginBottom: 10 }}>
                <div className="card-title" style={{ marginBottom: 0 }}>
                  Candidates
                </div>
                <span className="small faint">{candidates.length}</span>
              </div>
              <div className="row small" style={{ gap: 8, marginBottom: 10 }}>
                {["audio", "comments", "transcript", "user"].map((source) => (
                  <label key={source} className="row small" style={{ gap: 4 }}>
                    <input
                      type="checkbox"
                      checked={sources[source] !== false}
                      onChange={(event) =>
                        setSources((previous) => ({ ...previous, [source]: event.target.checked }))
                      }
                    />
                    {source}
                  </label>
                ))}
              </div>
              {!timeline?.analysis_available ? (
                <div className="hint">Run Analyze to add audio, comment, and transcript candidates.</div>
              ) : null}
              <div className="scroll-list">
                {candidates.length === 0 ? <div className="hint">No candidates.</div> : null}
                {candidates.map((candidate) => (
                  <div className="candidate-item" key={candidate.id}>
                    <div className="candidate-head">
                      <span className="candidate-title">{candidate.title}</span>
                      <span className={`badge ${candidate.source === "comments" ? "yellow" : "blue"}`}>
                        {candidate.source}
                      </span>
                    </div>
                    <div className="small faint">
                      {formatDuration(candidate.start)}–{formatDuration(candidate.end)} · score{" "}
                      {Math.round(candidate.score)}
                    </div>
                    <div className="candidate-why">{candidate.why}</div>
                    <div className="row">
                      <button className="btn btn-sm" onClick={() => addCandidate(candidate)}>
                        Add
                      </button>
                      <button className="btn btn-sm btn-ghost" onClick={() => seek(candidate.start)}>
                        Preview
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          <div className="card">
            <div className="row" style={{ justifyContent: "space-between", marginBottom: 10 }}>
              <div className="card-title" style={{ marginBottom: 0 }}>
                Selected moments
              </div>
              <span className="small faint">{moments.filter((moment) => moment.enabled).length} on</span>
            </div>
            <div className="scroll-list">
              {moments.length === 0 ? (
                <div className="hint">Add a candidate or place a clip at the playhead.</div>
              ) : null}
              {moments.map((moment, index) => (
                <div className={`moment-item ${moment.enabled ? "" : "disabled"}`} key={moment.id}>
                  <div className="row" style={{ justifyContent: "space-between" }}>
                    <input
                      className="input"
                      style={{ padding: "5px 8px", fontSize: 12 }}
                      value={moment.title}
                      onChange={(event) => updateMoment(index, { title: event.target.value })}
                    />
                    <button
                      className="icon-btn"
                      title={moment.enabled ? "Disable" : "Enable"}
                      onClick={() => updateMoment(index, { enabled: !moment.enabled })}
                    >
                      {moment.enabled ? "◉" : "○"}
                    </button>
                    <button
                      className="icon-btn"
                      title="Delete"
                      onClick={() => setMoments((previous) => previous.filter((_, itemIndex) => itemIndex !== index))}
                    >
                      ×
                    </button>
                  </div>
                  <div className="moment-inputs">
                    <label className="small faint">
                      Start
                      <input
                        className="input"
                        type="number"
                        step="0.1"
                        min={0}
                        max={Math.max(0, duration - 1)}
                        value={Number(moment.start.toFixed(2))}
                        onChange={(event) => updateMoment(index, { start: Number(event.target.value) })}
                      />
                    </label>
                    <label className="small faint">
                      End
                      <input
                        className="input"
                        type="number"
                        step="0.1"
                        min={moment.start + 1}
                        max={Math.min(duration, moment.start + 60)}
                        value={Number(moment.end.toFixed(2))}
                        onChange={(event) => updateMoment(index, { end: Number(event.target.value) })}
                      />
                    </label>
                  </div>
                  <div className="row small muted" style={{ justifyContent: "space-between" }}>
                    <span>{(moment.end - moment.start).toFixed(1)}s</span>
                    <button className="btn btn-sm btn-ghost" onClick={() => seek(moment.start)}>
                      Seek
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </aside>
      </div>
    </>
  );
}
