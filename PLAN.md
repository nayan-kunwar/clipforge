# ClipForge Web UI — Implementation Plan

**Status:** Ready for implementation  
**Date:** 2026-11-20  
**Target deployment:** `https://clipforge.nayankunwar.com`  
**Backend:** FastAPI + SQLAlchemy 2 + Alembic  
**Frontend:** React + TypeScript + Vite  
**Hosting:** Hostinger VPS, Ubuntu 26.04, Docker Compose

---

## 1. Goal and scope

Turn ClipForge from a local CLI into a self-hosted, multi-user web application while keeping the existing media-processing code and CLI.

The UI is the primary product surface. The CLI remains for automation and retains feature parity.

### In scope

- Invite-only accounts with email/password authentication
- Private per-user projects
- YouTube URL ingest and local file uploads
- Waveform timeline with human-in-the-loop clip selection and trimming
- Vertical 1080×1920 Shorts rendering
- Editable clip metadata and manifest generation
- Per-user YouTube uploads to each user's own channel, with daily caps
- Manual upload fallback (download + copy metadata)
- Lightweight custom admin interface with privacy-preserving visibility
- Public HTTPS deployment on a Hostinger VPS

### Out of scope for v1

- Open registration
- Public project sharing or social features
- Multi-instance / horizontal scaling
- Object storage (local disk is authoritative)
- Billing, subscriptions, or paid plans
- Automatic YouTube OAuth verification and quota-extension work
- Video editing beyond clip in/out and title overlay

---

## 2. Locked decisions

| Area | Decision |
| --- | --- |
| Hosting | Hostinger VPS, Ubuntu 26.04, 8 GB RAM, ~96 GB disk |
| Domain | Subdomain of `nayankunwar.com`; planned: `clipforge.nayankunwar.com` |
| DNS | Controlled by owner; A/AAAA records point to the VPS |
| TLS | Caddy with automatic Let's Encrypt certificates |
| Backend | FastAPI; existing Python media code reused |
| Frontend | React + TypeScript SPA, browser-only |
| Auth | Invite-only email/password, Argon2id, DB-backed sessions |
| Email | Brevo transactional API (SMTP fallback), plus copy-link invites |
| Projects | Private per user; admins do not see private media |
| Sources | YouTube URLs + local uploads |
| Upload cap | 4 GB per file, configurable |
| Duration cap | 2 hours per source, configurable |
| Storage quota | 15 GB per user, configurable |
| Upload automation | Capped per day; manual fallback always available |
| Admin visibility | Account metadata, job status, usage, sanitized errors; no media |
| Admin UI | Lightweight custom React interface |
| Job processing | SQLite-backed queue + separate media/upload workers |
| Database | SQLite with WAL on the VPS data volume |

---

## 3. Capacity and hardware assumptions

### Disk

96 GB total is not 96 GB of media:

| Item | Approx. |
| --- | --- |
| OS, Docker, images, logs | 12–15 GB |
| Headroom for DB/backups/temp | 5 GB |
| Usable media budget | ~75–78 GB |
| 15 GB/user × 5 users | 75 GB |

**Practical limit: 4–5 users at full quota.** Set `MAX_INVITED_USERS=5` for v1. If more users are needed, lower the per-user quota to 8–10 GB or attach block/object storage.

### CPU and memory

The 8 GB Hostinger tier is typically 2 vCPU. Planning assumption: **2 vCPU**, one media worker.

- 60 s 1080×1920 render at `veryfast`: roughly 1.5–3 minutes
- 8 clips: roughly 15–25 minutes of queue time
- Whisper `small` on 2 vCPU is not practical
- Default `skip_transcript=true` on the server
- If 3+ concurrent users are expected, upgrade to 4 vCPU

### Per-project storage estimate

| Artifact | Approx. |
| --- | --- |
| 4 GB source upload | 4 GB |
| 360p preview | 200–500 MB |
| Audio | 50–150 MB |
| Waveform/features | < 20 MB |
| Cached 1080p segments | 300 MB–1 GB |
| Rendered clips + thumbs | 200–500 MB |
| **Total** | **5–6 GB** |

15 GB/user allows roughly 2–3 active projects. Quota display and intermediate cleanup are required.

---

## 4. Architecture

```text
Internet
   │ HTTPS
Caddy  ── TLS, HSTS, security headers, SSE-safe proxying
   │
FastAPI web  ──────────────  SQLite (WAL)
  auth, projects,              users, sessions, invites,
  moments, clips,              projects, jobs, job_events,
  SSE, React static            uploads, oauth_tokens, quota
   │
   ├── /srv/clipforge/data
   │
Media worker  ── ingest, analyze, render   (ffmpeg, yt-dlp, whisper)
Upload worker ── per-user YouTube uploads  (Google API, quota)
```

### Components

| Component | Responsibility |
| --- | --- |
| Caddy | TLS, reverse proxy, security headers, SSE flushing |
| FastAPI web | JSON API, auth, SSE, React static files, media streaming |
| Media worker | Ingest, analysis, render jobs; CPU-heavy; concurrency 1 |
| Upload worker | YouTube uploads; network-bound; separate queue |
| SQLite | Application data and job queue |
| Data volume | Sources, previews, waveform, renders, manifests |
| React SPA | Primary user interface and admin UI |

### Why this shape

- Rendering and downloads take minutes; they must not block the API process.
- Separate workers survive web restarts.
- SQLite with WAL and job leases is sufficient for a single VPS and a small invited group.
- No Redis/Postgres in v1; upgrade path is documented if the app scales beyond one host.

---

## 5. Deployment

### 5.1 Hostinger VPS setup

- Ubuntu 26.04 (24.04 LTS is also acceptable if the provider image requires it)
- Non-root deploy user `clipforge`, SSH keys only
- UFW: allow 22 (ideally restricted to admin IP), 80, 443
- fail2ban enabled
- unattended-upgrades enabled
- Docker Engine and Compose plugin installed from Docker's apt repository (not snap)
- Application installed under `/opt/clipforge`
- Data owned by UID/GID used by containers under `/srv/clipforge/data`
- Swap: 2 GB

### 5.2 DNS and TLS

DNS records at the registrar:

| Type | Name | Value |
| --- | --- | --- |
| A | `clipforge` | VPS IPv4 |
| AAAA | `clipforge` | VPS IPv6 (if available) |
| CAA | `nayankunwar.com` | `0 issue "letsencrypt.org"` (only if CAA is used) |

Caddy obtains and renews certificates automatically once ports 80/443 are reachable.

### 5.3 Docker Compose services

```text
caddy          ports 80/443; automatic TLS
web            uvicorn; FastAPI + React static build
media-worker   python -m clipforge.worker --queue media
upload-worker  python -m clipforge.worker --queue upload
```

- Only Caddy publishes ports.
- All services share `/srv/clipforge/data`.
- Alembic migrations run on `web` startup before serving traffic; workers wait for the DB.
- Healthchecks: `/healthz` (liveness), `/readyz` (DB + data volume + disk).
- Log rotation: Docker `json-file`, 10 MB × 5.

### 5.4 Environment variables (`.env`, mode 600)

```dotenv
APP_ENV=production
PUBLIC_BASE_URL=https://clipforge.nayankunwar.com

SECRET_KEY=change-me
TOKEN_ENCRYPTION_KEY=change-me-fernet-key
DATABASE_URL=sqlite:////data/clipforge.db
DATA_DIR=/data

MEDIA_WORKER_CONCURRENCY=1
UPLOAD_WORKER_CONCURRENCY=2
MAX_INVITED_USERS=5

MAX_UPLOAD_BYTES=4294967296
MAX_SOURCE_SECONDS=7200
USER_STORAGE_BYTES=16106127360

UPLOAD_DAILY_GLOBAL=5
UPLOAD_DAILY_PER_USER=2
DEFAULT_SKIP_TRANSCRIPT=true
DEFAULT_FFMPEG_PRESET=veryfast

BREVO_API_KEY=
BREVO_SENDER_EMAIL=no-reply@nayankunwar.com
BREVO_SENDER_NAME=ClipForge
BREVO_SMTP_HOST=smtp-relay.brevo.com
BREVO_SMTP_PORT=587
BREVO_SMTP_USER=
BREVO_SMTP_PASSWORD=

GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=https://clipforge.nayankunwar.com/api/v1/oauth/google/callback

ADMIN_EMAIL=
ADMIN_INITIAL_PASSWORD=
LOG_LEVEL=INFO
```

`TOKEN_ENCRYPTION_KEY` is a Fernet key. Losing it invalidates stored Google refresh tokens; users must reconnect.

### 5.5 Backups

- Nightly SQLite online backup: `sqlite3 /data/clipforge.db ".backup '/backups/clipforge.db'"`
- Back up manifests and configuration; media is large and can be re-rendered from sources
- Offsite copy with restic/rclone (Hostinger Storage Box, Backblaze B2, etc.)
- Weekly Hostinger VPS snapshot
- Document and test a restore procedure

### 5.6 Monitoring and maintenance

- Uptime check on `/healthz`
- Disk usage alert at 80%
- Admin dashboard shows queue depth, failures, storage
- Job events retained 30 days
- Rebuild the image monthly to update yt-dlp and base packages
- Manual `docker compose pull && docker compose up -d` for application updates

---

## 6. External services

### 6.1 Brevo (email)

Use the Brevo transactional API via `httpx`:

```text
POST https://api.brevo.com/v3/smtp/email
header: api-key: <BREVO_API_KEY>
```

SMTP relay is a fallback only.

**Domain setup:**

- Verify `nayankunwar.com` in Brevo
- Add Brevo's SPF, DKIM, and DMARC records in Hostinger DNS
- Start DMARC with `p=none`, then tighten after confirming delivery
- Sender: `ClipForge <no-reply@nayankunwar.com>`

**Flows:**

| Flow | Token lifetime | Notes |
| --- | --- | --- |
| Invite | 7 days, single use | Email link + admin copy-link |
| Password reset | 1 hour, single use | Generic response to avoid account enumeration |
| Admin-triggered reset | 1 hour, single use | Sends the same reset email |

**Implementation notes:**

- Store only SHA-256 hashes of tokens
- Log delivery status in `email_log`
- Retry transient Brevo failures
- If email fails, the invite still works via copy-link
- Rate-limit email sends per user/IP

### 6.2 Google Cloud / YouTube

**OAuth client:** Web application.

**Redirect URI (exact):**

```text
https://clipforge.nayankunwar.com/api/v1/oauth/google/callback
```

**Authorized domain:** `nayankunwar.com`

**Scopes:**

```text
openid
email
https://www.googleapis.com/auth/youtube.upload
```

**Operational constraints:**

- `videos.insert` costs ~1,600 quota units
- Default YouTube Data API quota: 10,000 units/day for the whole Google Cloud project
- That is roughly 6 automated uploads/day across all users
- Default application caps: 5 global/day, 2 per user/day
- Quota resets at midnight Pacific Time
- Testing-mode consent screen: up to 100 test users; refresh tokens expire after 7 days
- Until the OAuth app is verified, Google may force uploads to Private
- Verification requires a homepage, privacy policy, terms, demo video, and scope justification

**Product behavior:**

- Each user connects their own Google account in Settings
- Refresh tokens are encrypted at rest with Fernet
- `invalid_grant` marks the connection broken and prompts reconnect
- Quota exhaustion returns a clear `429` with the next reset time
- Manual upload fallback is always available and consumes no API quota

### 6.3 yt-dlp

- Test YouTube downloads from the Hostinger VPS IP during Phase 0
- Datacenter IPs are more likely to trigger bot checks
- Keep yt-dlp current by rebuilding the image monthly
- Optional admin-provided `cookies.txt` via `YTDLP_COOKIES_FILE`
- Retry with fallback player clients (existing behavior)
- Local upload remains the fallback when server-side download fails

---

## 7. Storage and data model

### 7.1 Directory layout

```text
/srv/clipforge/
  .env
  docker-compose.yml
  Caddyfile
  data/
    clipforge.db
    uploads/<user_id>/<upload_id>/          # resumable chunks
    users/<user_id>/projects/<project_id>/
      source/                               # original upload or downloaded 360p
      preview.mp4                           # low-res review proxy
      audio.m4a
      waveform.json                         # 20 peaks/sec overview
      features.npz                          # optional cached audio features
      analysis.json                         # all raw candidates + suggestions
      moments.json                          # editable draft moments
      src1080/                              # cached 1080p windows
      shorts/                               # rendered clips
      thumbs/
      CLIPS_MANIFEST.json                   # CLI-compatible export
```

### 7.2 SQLite tables

```text
users
  id UUID PK, email UNIQUE, password_hash, role, is_active,
  created_at, last_login_at, storage_bytes, quota_bytes_override,
  upload_daily_cap_override

sessions
  id_hash PK, user_id FK, csrf_token, created_at, expires_at,
  last_seen_at, ip, user_agent

invites
  id, email NULL, token_hash UNIQUE, created_by, created_at,
  expires_at, accepted_at, revoked_at

password_resets
  id, user_id FK, token_hash, created_at, expires_at, used_at

projects
  id UUID PK, owner_id FK, title, source_type, source_ref,
  duration_s, status, options_json, source_bytes,
  created_at, updated_at

jobs
  id UUID PK, project_id NULL, user_id FK, kind, status, stage,
  progress, payload_json, error_code, error_detail, attempts,
  max_attempts, lease_until, cancel_requested,
  created_at, started_at, finished_at

job_events
  id, job_id FK, seq, level, message, progress, data_json, created_at
  UNIQUE (job_id, seq)

uploads
  id UUID PK, user_id FK, filename, content_type, size_bytes,
  received_bytes, sha256, status, created_at, expires_at

oauth_tokens
  user_id FK, provider, encrypted_refresh_token, account_email,
  channel_id, scopes, token_expires_at, status, updated_at

quota_usage
  date_pacific, user_id FK, units, uploads
  PRIMARY KEY (date_pacific, user_id)

admin_audit
  id, admin_id FK, action, target_type, target_id,
  metadata_json, created_at

email_log
  id, to_email, template, status, provider_id, error, created_at

app_settings
  key PK, value_json, updated_at
```

### 7.3 Project statuses

```text
uploading → ingesting → ready → analyzing → analyzed
                              → rendering → rendered
any stage → failed
```

- Timeline is usable immediately after `ready`.
- Analysis is optional and only adds candidate markers.
- Render failures are tracked per clip; the project can be `rendered` with failed clips visible.

### 7.4 Manifest

- Keep `CLIPS_MANIFEST.json` compatible with the CLI.
- Move to **project-relative paths** in manifest v2; CLI resolves them against the work directory.
- Include overlay title, suggested YouTube title/description/tags, source start, duration, render status, and YouTube/manual publish state.
- The UI always addresses media through clip IDs, never manifest paths.

### 7.5 Quota accounting

- Count source, preview, audio, waveform, segments, renders, thumbs.
- Reserve projected bytes on upload init; release on cancel/expiry.
- Maintain cached `storage_bytes`; reconcile nightly with a filesystem scan.
- Enforce before accepting an upload and before starting a render.
- Cleanup policy:
  - incomplete uploads: 24 hours
  - old render versions: after successful re-render
  - `src1080` segments: 7 days or under quota pressure
  - deleted projects: remove files immediately
- UI shows a per-user usage bar and blocks new projects when over quota.

---

## 8. Backend

### 8.1 File layout

```text
src/clipforge/
  config.py              environment + runtime settings
  db.py                  SQLAlchemy engine, WAL pragmas, sessions
  models.py              ORM models
  schemas.py             Pydantic API DTOs
  security.py            Argon2, token hashing, Fernet, CSRF
  deps.py                current_user, require_admin, ownership
  emailer.py             Brevo client, templates, retries
  storage.py             per-user paths, quotas, cleanup
  pipeline.py            shared ingest/analyze/render/upload orchestration
  waveform.py            peak generation + range detail
  jobs.py                queue claim/lease/heartbeat/events/cancel
  api/
    main.py              app factory, middleware, static SPA
    auth.py              login/logout/me/invite/reset/password
    projects.py          project CRUD, timeline, analyze/render triggers
    uploads.py           resumable chunk upload
    moments.py           bulk save + validation
    clips.py             list, metadata, re-render, manual export
    jobs.py              status + SSE
    media.py             owner-only Range-served media
    oauth_google.py      OAuth start/callback/disconnect
    admin.py             sanitized admin endpoints
    public.py            health, privacy, terms
  worker/
    __main__.py          worker loop
    runner.py            job dispatch, progress, cancellation
  migrations/            Alembic
  cli.py                 existing commands + serve/worker/user helpers
web/                     React application
```

### 8.2 Shared pipeline

All surfaces call one service layer:

```python
pipeline.ingest_source(source, project, options, ctx)
pipeline.analyze(project, options, ctx)
pipeline.render(project, moments, options, ctx)
pipeline.upload(project, clips, user_credentials, options, ctx)
```

`JobContext` provides:

- `emit(level, message, progress=None, data=None)`
- `cancel_event`
- `run_process(cmd, ...)` wrapper that registers the subprocess for cancellation
- access to project paths and settings

The CLI passes a console emitter; the worker persists events to `job_events`.

### 8.3 Job queue

- SQLite WAL, `busy_timeout`, foreign keys enabled
- Claim with `UPDATE ... RETURNING` inside `BEGIN IMMEDIATE`
- 30-second lease, heartbeat every 10 seconds
- Max 2 attempts
- Cancellation checked between stages and during ffmpeg/yt-dlp execution
- Startup recovery: expired leases requeue or mark interrupted
- Per-user active job cap: 2
- Media worker concurrency: 1
- Upload worker concurrency: 2
- One active job per `(project_id, kind)` using a partial unique index
- Events persisted with per-job sequence numbers

### 8.4 Progress reporting

| Stage | Source |
| --- | --- |
| Ingest | yt-dlp percentage / step transitions |
| Analyze | audio decode, comment clusters, transcript window N/M |
| Render | `ffmpeg -progress pipe:1`, per clip |
| Upload | `next_chunk()` percentage, per clip |

### 8.5 API

Base path: `/api/v1`

```text
POST   /auth/login
POST   /auth/logout
GET    /auth/me
POST   /auth/accept-invite
POST   /auth/forgot-password
POST   /auth/reset-password
POST   /auth/change-password
GET    /auth/sessions
DELETE /auth/sessions/{id}

GET    /projects
POST   /projects                         {youtube_url | upload_id, options}
GET    /projects/{id}
DELETE /projects/{id}
GET    /projects/{id}/timeline
GET    /projects/{id}/waveform?start&end&buckets
POST   /projects/{id}/analyze
PATCH  /projects/{id}/moments            bulk upsert/delete
POST   /projects/{id}/render             {moment_ids, fps, ...}
GET    /projects/{id}/clips
GET    /projects/{id}/manifest           download JSON

PATCH  /clips/{id}                       overlay/title/description/tags
POST   /clips/{id}/rerender
POST   /projects/{id}/upload             {clip_ids, privacy}
POST   /clips/{id}/mark-manual

POST   /uploads                          init resumable upload
GET    /uploads/{id}                     resume offset
PUT    /uploads/{id}                     chunk with Content-Range
POST   /uploads/{id}/complete
DELETE /uploads/{id}

GET    /jobs/{id}
GET    /jobs/{id}/events                 SSE, Last-Event-ID
POST   /jobs/{id}/cancel

GET    /oauth/google/start
GET    /oauth/google/callback
POST   /oauth/google/disconnect

GET    /admin/users
GET    /admin/users/{id}
PATCH  /admin/users/{id}                 disable/enable, quota overrides
POST   /admin/users/{id}/reset-password
DELETE /admin/users/{id}/sessions
GET    /admin/invites
POST   /admin/invites
DELETE /admin/invites/{id}
POST   /admin/invites/{id}/resend
GET    /admin/jobs
POST   /admin/jobs/{id}/cancel
POST   /admin/jobs/{id}/retry
GET    /admin/stats
GET    /admin/errors
GET    /admin/settings
PATCH  /admin/settings
GET    /admin/audit
```

Public routes:

```text
GET /healthz
GET /readyz
GET /privacy
GET /terms
```

Error envelope:

```json
{
  "error": {
    "code": "quota_exceeded",
    "message": "Human-readable message",
    "details": {}
  }
}
```

Status codes: `400`, `401`, `403`, `404`, `409`, `413`, `422`, `429`, `500`.

### 8.6 Authentication and sessions

- Invite-only; no registration endpoint
- Admin creates an invite; user accepts and sets a password
- Argon2id password hashing; minimum 10 characters
- Sessions stored hashed in the DB
- Cookie: `__Host-clipforge_session`, `Secure`, `HttpOnly`, `SameSite=Lax`, `Path=/`, 30-day sliding expiry
- CSRF: `__Host-clipforge_csrf` cookie + `X-CSRF-Token` header on mutations
- Password change revokes all other sessions
- Login rate limit: 5 attempts / 15 minutes per IP + account, with backoff
- Forgot password always returns a generic response
- First admin bootstrapped from `ADMIN_EMAIL` / `ADMIN_INITIAL_PASSWORD`, forced to change password
- Optional `clipforge user create-admin` CLI for recovery

### 8.7 Uploads

- `POST /uploads` with filename, size, optional SHA-256
- Preflight: auth, quota, max size, free disk, max invited users
- Chunk size: 16 MB
- Caddy body limit: 20 MB per chunk
- `PUT /uploads/{id}` validates `Content-Range` and appends
- `GET /uploads/{id}` returns received bytes for resume
- `POST /uploads/{id}/complete`:
  - verify size
  - ffprobe: video stream present, duration ≤ 2 hours
  - enforce quota with actual bytes
  - move into project storage
  - create project and enqueue ingest
  - reject and delete on failure
- Abandoned uploads expire after 24 hours
- Filenames sanitized; storage paths generated server-side

### 8.8 Ingest

YouTube source:

1. Validate host against YouTube URL patterns
2. Fetch metadata and comments
3. Reject sources longer than 2 hours
4. Download ≤360p analysis video + audio
5. Generate preview, waveform, poster
6. Persist metadata/comments and mark `ready`

Local upload source:

1. Move source into project storage
2. Probe and validate
3. Generate 360p preview, audio, waveform, poster
4. Persist metadata and mark `ready`

Details:

- Preview: H.264/AAC, faststart, suitable for timeline scrubbing
- Audio: AAC 128 kbps stereo 44.1 kHz
- Waveform: 20 peaks/sec overview; higher resolution on demand
- Cache audio features (`features.npz`) so analysis does not decode twice
- Ingest is idempotent enough to resume after a crash

### 8.9 Analysis

Signals:

- Audio energy peaks (existing `detect.py`)
- Comment timestamp clusters
- Transcript keyword hits (optional, disabled by default on this VPS)
- User-provided timestamps

Output:

- `analysis.json` with all raw candidates and metadata
- Selected suggestions from `merge_and_rank`
- `highlight_candidates.json` kept for CLI compatibility
- Re-analysis does not overwrite user-edited moments unless explicitly requested

Transcript:

- Only candidate windows, not the full video
- Default model: `base` if enabled; configurable
- Clear error if faster-whisper is not installed
- CPU-heavy; one transcript job at a time

### 8.10 Render

- Validate each moment: 1–60 seconds, within source bounds
- Source selection:
  - local upload: original file
  - YouTube: cached 1080p segment, downloaded with padding
  - fallback: 360p preview
- Output: 1080×1920, blurred background, foreground scaled to width
- Title overlay: first 3.5 seconds, existing font handling
- H.264 High profile, AAC 192 kbps, yuv420p, faststart
- Default preset `veryfast` on the VPS
- Per-clip progress and cancellation
- Verify output with ffprobe; write thumbnail
- Versioned render filenames; update manifest and clip record
- Continue other clips if one fails; surface per-clip errors

### 8.11 YouTube upload

- One job per clip
- Build credentials from the encrypted per-user refresh token
- Enforce quota before the API call
- Default privacy: Private
- Allowed privacy: Private, Unlisted, Public (with warnings)
- Validate title ≤100 chars, description ≤5000, tags ≤500 total chars
- Update manifest with `youtube_id`, `youtube_url`, privacy, timestamp
- Manual fallback:
  - Download MP4
  - Copy title/description/tags
  - Open YouTube Studio
  - Optional "mark as manually uploaded"
- Quota manager:
  - 1,600 units per insert
  - global daily cap 5 (leaves margin)
  - per-user daily cap 2
  - reset at midnight `America/Los_Angeles`
  - admin can adjust caps
- OAuth failures:
  - `invalid_grant` → mark token broken, prompt reconnect
  - quota exceeded → actionable message with reset time
  - upload limit / channel restriction → show API error summary

### 8.12 Media serving

- Owner-only access checks on every request
- Admin denied for projects they do not own
- UUID identifiers, no path inputs from clients
- HTTP Range support for video seeking
- `Content-Disposition: attachment` for downloads
- Cache-busting version query after re-render
- No directory listing

### 8.13 Admin API privacy

Admin responses are sanitized DTOs:

- Allowed: user email, role, active state, timestamps, storage usage, quota, project IDs/status/counts, job kind/status/progress/duration, sanitized error summaries, invite/email status
- Denied: project titles, thumbnails, source URLs, transcript text, clip media, manifests
- Media endpoints deny admins for other users' projects
- Error sanitization strips filesystem paths, source URLs, and transcript snippets
- All admin mutations write to `admin_audit`

---

## 9. Frontend

### 9.1 Stack

- React + TypeScript
- Vite
- React Router
- TanStack Query
- Tailwind CSS + shadcn/ui
- wavesurfer.js
- zod + react-hook-form
- openapi-typescript for generated API types

### 9.2 Routes

```text
/login
/invite/:token
/                    Library
/new                 New project
/p/:id               Project review (timeline)
/p/:id/clips         Clip review
/settings            Google, password, sessions, quota
/admin               Admin dashboard
/admin/users
/admin/invites
/admin/jobs
/admin/usage
/admin/settings
/admin/errors
```

### 9.3 Timeline

- Overview waveform at 20 peaks/sec
- Range endpoint for high-resolution detail when zoomed
- Candidate markers colored by source:
  - audio peak
  - comment cluster
  - transcript hit
  - user timestamp
- Tooltips: score, reason, comment count/likes, transcript snippet
- Draggable regions for selected moments
- Touch-friendly trim handles
- Two-way sync with the 360p preview video
- Click/drag timeline seeks video; video time updates playhead
- Add clip at playhead or from a candidate
- Enable/disable, delete, undo/redo
- Enforce 1–60 seconds and source bounds client and server side
- Debounced autosave to `moments.json`
- Reload or another device preserves work
- Keyboard shortcuts: space, I/O, delete, undo/redo

### 9.4 Project review screen

- Source preview player
- Timeline
- Candidate list with filters
- Selected moment list with inline title editing
- Analyze and Render actions
- Job progress drawer with live logs and cancel
- Storage/queue warnings

### 9.5 Clip review screen

- 9:16 clip grid with thumbnail previews
- Detail drawer with player
- Metadata editor: overlay title, YouTube title, description, tags
- Re-render when overlay or trim changes
- Download MP4
- Automated upload (if connected and quota available)
- Manual fallback actions
- Per-clip status: rendered, stale, failed, uploaded, manual

### 9.6 Upload UX

- Drag-and-drop or file picker
- Client-side preflight for 4 GB limit and 2-hour duration (server remains authoritative)
- Resumable chunks with progress and pause/resume
- Warn on mobile that background uploads may pause
- Show remaining user storage before starting
- Clear error for quota, size, duration, unsupported codec

### 9.7 Admin UI

- Users: email, role, active, created, last login, storage, quota, project counts
- Invites: create, copy link, resend, revoke, status
- Jobs: queue/running/failed, kind, user, project ID, progress, duration, sanitized error
- Usage: disk total, per-user, largest projects, cleanup actions
- Settings: limits, upload caps, worker concurrency, transcript default
- Errors: recent failures with sanitized messages
- No media, titles, thumbnails, or transcript content

### 9.8 State and data fetching

- TanStack Query for server state
- SSE hook updates job cache and invalidates project/clip queries on completion
- Local Zustand store for timeline editing and undo/redo
- Optimistic moment saves with debounce
- Generated TypeScript types from OpenAPI
- Toast notifications + inline errors

### 9.9 Responsive behavior

- Library and clip review usable on phones
- Timeline supports touch drag and pinch/zoom where practical
- Upload flow warns about background limits
- Desktop is the primary editing experience

---

## 10. Limits and enforcement

| Setting | Default | Enforcement |
| --- | --- | --- |
| Max upload | 4 GB | preflight + server on complete |
| Max source duration | 2 hours | ffprobe / yt-dlp metadata |
| Storage per user | 15 GB | projected usage + actual usage |
| Max invited users | 5 | invite creation |
| Automated uploads/day | 5 global, 2 per user | quota manager |
| Active jobs per user | 2 | job creation |
| Media worker concurrency | 1 | worker config |
| Upload worker concurrency | 2 | worker config |
| Default render preset | `veryfast` | settings |
| Default transcription | off | settings |
| Chunk size | 16 MB | upload API |
| Incomplete upload TTL | 24 hours | cleanup |
| Job event retention | 30 days | cleanup |

All limits are configurable through environment variables and admin settings, with env as the default and DB overrides taking precedence.

---

## 11. Security

### Application

- Argon2id passwords
- DB-backed sessions; hashed session IDs
- Secure, HttpOnly, SameSite cookies
- CSRF token on all state-changing requests
- Login and email rate limiting
- Generic forgot-password response
- Admin audit log
- Per-user ownership checks on projects, moments, clips, jobs, uploads, and media
- Admin denied private media
- UUID identifiers
- Path traversal protection; no client-supplied paths

### Deployment

- Caddy TLS with automatic renewal
- HSTS, CSP, X-Content-Type-Options, Referrer-Policy, Permissions-Policy
- Only 22/80/443 exposed; 22 restricted where possible
- Secrets only in `.env`, mode 600
- Containers run as non-root where possible
- Data volume permissions checked at startup
- Nightly DB backups, offsite copy, restore test
- Dependency and base-image updates
- Disk quota and upload limits
- Log redaction for tokens, cookies, and signed URLs

### Content

- Only YouTube URLs are accepted for URL ingest (no arbitrary URL fetch)
- Uploaded files are probed, never executed
- Require a "I own or have permission to use this content" confirmation before ingest
- Add `/privacy` and `/terms` pages (also required for Google OAuth verification)
- Document copyright responsibility in the README and UI

---

## 12. CLI parity

The existing CLI remains functional:

```bash
clipforge run
clipforge ingest
clipforge detect
clipforge render
clipforge upload
```

New commands:

```bash
clipforge serve          # local/dev web server
clipforge worker         # run a worker process
clipforge user create-admin
clipforge user invite
```

Shared pipeline guarantees UI and CLI behavior match.

| CLI flag | UI equivalent |
| --- | --- |
| `--max-len` | Clip settings |
| `--count` | "Suggest N moments" |
| `--timestamps` | Seed user moments |
| `--keywords` | Transcript keywords |
| `--skip-transcript` | Analyze toggle |
| `--whisper-model` | Advanced analysis setting |
| `--min-separation` | Advanced analysis setting |
| `--fps` | Render settings |
| `--crf` | Render settings |
| `--preset` | Render settings |
| `--font` | Font selection |
| `--no-comments` | Ingest option |
| `--skip-1080` | Render source option |
| `--privacy` | Upload dialog |
| `--category-id` | Upload dialog |

---

## 13. Testing and CI

### Backend

- Unit tests: timestamps, waveform buckets, moment validation, quota math, email tokens, path safety
- Integration tests: auth, invites, password reset, ownership isolation, upload resume, job lease/cancel, SSE replay, admin privacy
- Worker tests with fake ffmpeg/yt-dlp binaries; one real smoke render
- Media Range request tests
- Alembic migration tests

### Frontend

- Vitest for timeline math and state reducers
- Playwright E2E:
  - invite → accept → login
  - create URL project → ingest → timeline → trim → render → clip preview
  - upload/resume
  - admin cannot open media
  - quota and size errors
- Accessibility basics: keyboard navigation, focus states

### CI

- Backend: ruff, mypy, pytest
- Frontend: eslint, `tsc --noEmit`, vitest, build
- OpenAPI type generation check
- Docker image build
- Optional: deploy workflow with manual approval

---

## 14. Milestones

| Phase | Deliverable | Estimate |
| --- | --- | --- |
| 0 | VPS hardening, DNS/Caddy, Brevo domain, Google OAuth client, repo scaffold | 2–3 days + propagation |
| 1 | Core refactor: shared pipeline, JobContext, waveform, analysis JSON, tests; CLI unchanged | 5–7 days |
| 2 | DB/auth/invites/email/uploads/jobs/SSE/media + admin API | 8–10 days |
| 3 | React shell: login, invite, library, new project, live progress | 5–7 days |
| 4 | Timeline: waveform, candidates, trim, autosave, video sync, mobile | 7–9 days |
| 5 | Render queue, clip grid, metadata, re-render, manual export | 5–7 days |
| 6 | Per-user Google OAuth, capped uploads, quota manager, manual fallback | 4–6 days |
| 7 | Admin UI, deployment, backups, hardening, E2E | 5–7 days |

**Total: roughly 8–11 weeks full-time**, plus DNS/Brevo/OAuth setup and any Google verification wait.

### First vertical slice

Build this before uploads:

```text
login → create YouTube project → ingest → timeline visible
→ trim one moment → render → preview clip
```

This validates the hardest UX early.

---

## 15. Definition of done

- [ ] `https://clipforge.nayankunwar.com` serves valid TLS and redirects HTTP to HTTPS
- [ ] Invite email arrives; accept invite; login; password reset works
- [ ] A 2-hour YouTube project and a 3.5 GB local upload both ingest correctly
- [ ] Timeline loads, trim saves, survives reload, renders the exact selected range
- [ ] 4 GB / 2 hour / 15 GB limits enforced with clear errors
- [ ] Storage and upload quotas visible to users
- [ ] User A cannot access User B's project or media
- [ ] Admin cannot access private media or titles/transcripts
- [ ] Worker restart during a render recovers cleanly
- [ ] Daily upload cap enforced; manual fallback works without any API call
- [ ] Backup restore tested
- [ ] Mobile timeline and clip review usable
- [ ] Admin can disable users, reset passwords, adjust quotas, and inspect sanitized errors
- [ ] No secrets in git or logs

---

## 16. Risks and mitigations

| Risk | Impact | Mitigation |
| --- | --- | --- |
| YouTube blocks yt-dlp from VPS IP | URL ingest fails | Test in Phase 0; cookies file; local upload fallback |
| OAuth testing tokens expire every 7 days | Uploads fail weekly | Reconnect prompt; manual fallback; plan verification |
| Shared YouTube API quota (~6/day) | Upload queues block | Global/per-user caps; quota UI; manual fallback |
| 2 vCPU is slow for renders/whisper | Long queues | One media worker; `veryfast`; transcript off by default; upgrade option |
| 96 GB disk with 15 GB/user | Max 4–5 users | Invite cap; quota display; intermediary cleanup; block storage |
| 4 GB uploads over mobile | Failed uploads | Resumable chunks; warnings; desktop recommendation |
| Brevo deliverability | Invites/resets lost | SPF/DKIM/DMARC; copy-link invites; email log |
| Public VPS abuse | Resource exhaustion / legal | Invite-only, rate limits, quotas, copyright confirmation |
| SQLite contention | Slow API under load | WAL, short transactions, single-node design |
| Job loss on restart | Stalled projects | Leases, heartbeat, startup recovery, idempotent stages |
| Admin privacy leak | Trust breach | Sanitized DTOs, media denial, audit log, tests |

---

## 17. Open items

1. Confirm the subdomain name (`clipforge.nayankunwar.com` is the working assumption).
2. Confirm the VPS vCPU count; this determines whether transcription is enabled by default.
3. Choose an offsite backup destination (Hostinger Storage Box, Backblaze B2, etc.).
4. Confirm whether the default 5-user cap is acceptable.
5. Confirm branding: app name/logo/colors for the UI and Brevo emails.
6. Decide whether admins may ever view project titles (current plan: no).
7. Confirm the Google OAuth app verification plan (stay in Testing + manual fallback, or pursue verification).

---

## Appendix A — Request flow examples

### YouTube project

```text
POST /api/v1/projects
  { "youtube_url": "...", "options": { ... } }
→ project created, ingest job queued
→ worker downloads metadata/comments/360p/audio
→ preview + waveform generated, status=ready
→ GET /projects/{id}/timeline
→ PATCH /projects/{id}/moments
→ POST /projects/{id}/render { moment_ids }
→ worker renders clips, updates manifest
→ GET /projects/{id}/clips
→ POST /projects/{id}/upload { clip_ids, privacy }
  or manual download/copy flow
```

### Local upload

```text
POST /api/v1/uploads { filename, size }
→ upload_id + chunk size
PUT /api/v1/uploads/{id} (chunks)
POST /api/v1/uploads/{id}/complete
→ validation, project creation, ingest job
```

---

## Appendix B — Repository layout target

```text
clipforge/
  PLAN.md
  README.md
  pyproject.toml
  Dockerfile
  docker-compose.yml
  Caddyfile
  .env.example

  src/clipforge/
    __init__.py
    cli.py
    config.py
    db.py
    models.py
    schemas.py
    security.py
    deps.py
    emailer.py
    storage.py
    pipeline.py
    waveform.py
    jobs.py
    ingest.py
    detect.py
    render.py
    manifest.py
    upload.py
    util.py
    api/
    worker/
    migrations/

  web/
    package.json
    vite.config.ts
    tsconfig.json
    src/
      main.tsx
      router.tsx
      api/
      hooks/
      components/
      routes/
      styles/
    e2e/

  tests/
    test_pipeline.py
    test_auth.py
    test_projects.py
    test_jobs.py
    test_uploads.py
    test_media_access.py
    test_admin_privacy.py
    test_quota.py

  scripts/
    backup.sh
    restore.md
```

---

*This plan is a living document. Update it when deployment details, limits, or scope change.*
