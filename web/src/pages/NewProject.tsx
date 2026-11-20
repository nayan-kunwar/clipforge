import { useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";

import { api, formatBytes } from "../api";
import type { Project, Job } from "../types";

const MAX_UPLOAD = 4 * 1024 * 1024 * 1024;

export function NewProjectPage() {
  const navigate = useNavigate();
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [mode, setMode] = useState<"youtube" | "upload">("youtube");
  const [url, setUrl] = useState("");
  const [title, setTitle] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [maxLen, setMaxLen] = useState(60);
  const [count, setCount] = useState(8);
  const [skipTranscript, setSkipTranscript] = useState(true);
  const [fetchComments, setFetchComments] = useState(true);
  const [fps, setFps] = useState(30);
  const [crf, setCrf] = useState(19);
  const [preset, setPreset] = useState("veryfast");
  const [advanced, setAdvanced] = useState(false);
  const [busy, setBusy] = useState(false);
  const [uploaded, setUploaded] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const options = {
    max_len: maxLen,
    count,
    skip_transcript: skipTranscript,
    fetch_comments: fetchComments,
    fps,
    crf,
    preset,
  };

  const submitUpload = async (selected: File): Promise<{ project: Project; job: Job }> => {
    if (selected.size > MAX_UPLOAD) {
      throw new Error(`File exceeds the ${formatBytes(MAX_UPLOAD)} limit`);
    }
    const upload = await api.uploads.init(selected.name, selected.size);
    setUploaded(0);
    let offset = 0;
    while (offset < selected.size) {
      const chunk = selected.slice(offset, Math.min(selected.size, offset + upload.chunk_size));
      const updated = await api.uploads.putChunk(upload.id, offset, chunk);
      offset = updated.received_bytes;
      setUploaded(offset);
    }
    const completed = await api.uploads.complete(upload.id);
    if (completed.status !== "complete") {
      throw new Error("Upload did not complete");
    }
    return api.projects.create({
      upload_id: upload.id,
      title: title || selected.name.replace(/\.[^.]+$/, ""),
      options,
    });
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const result =
        mode === "youtube"
          ? await api.projects.create({
              youtube_url: url.trim(),
              title: title || undefined,
              options,
            })
          : await (() => {
              if (!file) throw new Error("Choose a video file first");
              return submitUpload(file);
            })();
      navigate(`/p/${result.project.id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create project");
    } finally {
      setBusy(false);
    }
  };

  const uploadPercent = file ? Math.round((uploaded / Math.max(1, file.size)) * 100) : 0;

  return (
    <>
      <div className="page-header">
        <div>
          <h1>New project</h1>
          <p>Import a YouTube URL or upload a local video. Max 2 hours, 4 GB.</p>
        </div>
      </div>

      <form onSubmit={submit} className="stack" style={{ maxWidth: 860 }}>
        <div className="tabs">
          <button type="button" className={mode === "youtube" ? "active" : ""} onClick={() => setMode("youtube")}>
            YouTube URL
          </button>
          <button type="button" className={mode === "upload" ? "active" : ""} onClick={() => setMode("upload")}>
            Upload file
          </button>
        </div>

        {mode === "youtube" ? (
          <div className="field">
            <label>YouTube URL</label>
            <input
              className="input"
              placeholder="https://www.youtube.com/watch?v=..."
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              required
            />
            <span className="hint">
              Only videos you own or have permission to use. Downloads run server-side.
            </span>
          </div>
        ) : (
          <div className="field">
            <label>Video file</label>
            <input
              ref={fileRef}
              className="input"
              type="file"
              accept="video/*,.mkv,.webm,.mov"
              onChange={(event) => {
                setUploaded(0);
                setFile(event.target.files?.[0] || null);
              }}
              required
            />
            {file ? (
              <span className="hint">
                {file.name} · {formatBytes(file.size)}
              </span>
            ) : (
              <span className="hint">MP4, MOV, MKV, or WebM. Up to 4 GB and 2 hours.</span>
            )}
            {busy && file && uploaded > 0 ? (
              <div>
                <div className="progress quota-bar">
                  <div style={{ width: `${uploadPercent}%` }} />
                </div>
                <span className="hint">
                  Uploading {formatBytes(uploaded)} / {formatBytes(file.size)}
                </span>
              </div>
            ) : null}
          </div>
        )}

        <div className="field">
          <label>Project title (optional)</label>
          <input
            className="input"
            placeholder="Defaults to the source title or filename"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
          />
        </div>

        <div className="card">
          <div className="card-title">Detection options</div>
          <div className="split">
            <div className="field">
              <label>Max clip length (seconds)</label>
              <input
                className="input"
                type="number"
                min={1}
                max={60}
                value={maxLen}
                onChange={(event) => setMaxLen(Math.max(1, Math.min(60, Number(event.target.value) || 1)))}
              />
            </div>
            <div className="field">
              <label>Suggested clip count</label>
              <input
                className="input"
                type="number"
                min={1}
                max={50}
                value={count}
                onChange={(event) => setCount(Math.max(1, Math.min(50, Number(event.target.value) || 1)))}
              />
            </div>
          </div>
          <div className="row" style={{ marginTop: 12 }}>
            <label className="row small">
              <input
                type="checkbox"
                checked={skipTranscript}
                onChange={(event) => setSkipTranscript(event.target.checked)}
              />
              Skip transcript cues
            </label>
            <label className="row small">
              <input
                type="checkbox"
                checked={fetchComments}
                onChange={(event) => setFetchComments(event.target.checked)}
              />
              Fetch YouTube comments
            </label>
          </div>
        </div>

        <button type="button" className="btn btn-ghost" onClick={() => setAdvanced((value) => !value)}>
          {advanced ? "Hide render options" : "Show render options"}
        </button>
        {advanced ? (
          <div className="card">
            <div className="card-title">Render options</div>
            <div className="split">
              <div className="field">
                <label>Frame rate</label>
                <select className="select" value={fps} onChange={(event) => setFps(Number(event.target.value))}>
                  <option value={30}>30 fps</option>
                  <option value={60}>60 fps</option>
                </select>
              </div>
              <div className="field">
                <label>CRF (quality)</label>
                <input
                  className="input"
                  type="number"
                  min={0}
                  max={51}
                  value={crf}
                  onChange={(event) => setCrf(Math.max(0, Math.min(51, Number(event.target.value) || 19)))}
                />
              </div>
              <div className="field">
                <label>Preset</label>
                <select className="select" value={preset} onChange={(event) => setPreset(event.target.value)}>
                  <option value="ultrafast">ultrafast</option>
                  <option value="veryfast">veryfast</option>
                  <option value="faster">faster</option>
                  <option value="medium">medium</option>
                  <option value="slow">slow</option>
                </select>
              </div>
            </div>
          </div>
        ) : null}

        {error ? <div className="error-text">{error}</div> : null}
        <div className="row">
          <button className="btn btn-primary" type="submit" disabled={busy}>
            {busy ? "Working…" : "Create project"}
          </button>
          <span className="hint">Ingest starts immediately and appears in the library.</span>
        </div>
      </form>
    </>
  );
}
