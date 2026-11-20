import { useEffect, useRef, useState } from "react";

import { api } from "../api";
import type { Job, JobEvent } from "../types";

interface JobPanelProps {
  jobId: string | null;
  onDone?: () => void;
  onError?: (message: string) => void;
}

export function JobPanel({ jobId, onDone, onError }: JobPanelProps) {
  const [job, setJob] = useState<Job | null>(null);
  const [logs, setLogs] = useState<JobEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const doneRef = useRef(false);

  useEffect(() => {
    if (!jobId) {
      setJob(null);
      setLogs([]);
      setError(null);
      return;
    }
    doneRef.current = false;
    setJob(null);
    setLogs([]);
    setError(null);
    const source = new EventSource(api.jobs.eventsUrl(jobId));
    source.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data) as JobEvent;
        if (!data || typeof data.seq !== "number") return;
        setLogs((previous) => [...previous.slice(-199), data]);
        if (data.progress !== null) {
          setJob((previous) =>
            previous
              ? { ...previous, progress: data.progress ?? previous.progress, stage: data.stage || previous.stage }
              : previous,
          );
        }
      } catch {
        // ignore malformed event
      }
    };
    source.addEventListener("done", () => {
      source.close();
      if (!doneRef.current) {
        doneRef.current = true;
        void api.jobs.get(jobId).then(setJob).catch(() => undefined);
        onDone?.();
      }
    });
    source.onerror = () => {
      // EventSource reconnects automatically. Poll once for a terminal state.
      void api.jobs
        .get(jobId)
        .then((current) => {
          setJob(current);
          if (["succeeded", "failed", "cancelled"].includes(current.status) && !doneRef.current) {
            doneRef.current = true;
            onDone?.();
          }
          if (current.status === "failed" && current.error_detail) {
            setError(current.error_detail);
            onError?.(current.error_detail);
          }
        })
        .catch(() => undefined);
    };
    return () => source.close();
  }, [jobId, onDone, onError]);

  if (!jobId) return null;

  const progress = Math.max(0, Math.min(1, job?.progress ?? 0));
  const status = job?.status || "queued";
  return (
    <div className="job-panel">
      <div className="job-head">
        <div className="row" style={{ gap: 8 }}>
          <span className={`badge ${status === "failed" ? "red" : status === "succeeded" ? "green" : "blue"}`}>
            {status}
          </span>
          <span className="small muted">{job?.stage || job?.kind || "job"}</span>
        </div>
        {status === "running" || status === "queued" ? (
          <button
            className="btn btn-sm btn-danger"
            onClick={() => {
              void api.jobs.cancel(jobId).catch((err: Error) => setError(err.message));
            }}
          >
            Cancel
          </button>
        ) : null}
      </div>
      <div className="progress">
        <div style={{ width: `${Math.round(progress * 100)}%` }} />
      </div>
      <div className="row small muted" style={{ justifyContent: "space-between" }}>
        <span>{Math.round(progress * 100)}%</span>
        <span>{job?.kind}</span>
      </div>
      {error ? <div className="error-text">{error}</div> : null}
      <div className="job-logs">
        {logs.length === 0 ? <span className="faint">Waiting for events…</span> : null}
        {logs.map((event) => (
          <div key={event.seq}>
            <span className="faint">[{event.level}]</span> {event.message}
          </div>
        ))}
      </div>
    </div>
  );
}
