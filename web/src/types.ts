export interface User {
  id: string;
  email: string;
  role: "admin" | "member";
  created_at: string;
  last_login_at: string | null;
}

export interface Quota {
  used_bytes: number;
  quota_bytes: number;
}

export interface GoogleStatus {
  connected: boolean;
  account_email: string | null;
  channel_id: string | null;
  status: string | null;
}

export interface UploadQuota {
  date_pacific: string;
  units_used: number;
  units_limit: number;
  uploads_used: number;
  uploads_limit: number;
  user_units_used: number;
  user_uploads_limit: number;
  estimated_uploads_left: number;
}

export interface AuthPayload {
  user: User;
  csrf: string;
  quota: Quota;
  google: GoogleStatus;
  upload_quota: UploadQuota;
}

export interface Project {
  id: string;
  title: string;
  source_type: "youtube" | "upload";
  source_url: string | null;
  duration_s: number;
  status: string;
  created_at: string;
  updated_at: string;
  options: Record<string, unknown>;
  preview_url: string | null;
  poster_url: string | null;
  waveform_url: string | null;
  moment_count: number;
  clip_count: number;
  storage_bytes: number;
  latest_job?: Job | null;
}

export interface Candidate {
  id: string;
  source: string;
  start: number;
  end: number;
  duration: number;
  peak: number;
  score: number;
  title: string;
  why: string;
  comment_count?: number;
  comment_likes?: number;
  text?: string;
}

export interface Moment {
  id: string;
  start: number;
  end: number;
  peak: number;
  title: string;
  source: string;
  score: number;
  why: string;
  enabled: boolean;
}

export interface Waveform {
  version: number;
  buckets_per_second: number;
  duration_s: number;
  peaks: number[][];
}

export interface Timeline {
  project_id: string;
  duration_s: number;
  status: string;
  analysis_available: boolean;
  waveform: Waveform | null;
  candidates: Candidate[];
  moments: Moment[];
}

export interface Clip {
  id: string;
  file_url: string;
  download_url: string;
  thumb_url: string;
  src_start: string;
  src_start_sec: number;
  length_s: number;
  overlay: string;
  why: string;
  sources: string[];
  score: number;
  suggested_title: string;
  suggested_description: string;
  tags: string[];
  ok: boolean;
  youtube_id?: string | null;
  youtube_url?: string | null;
  privacy?: string | null;
  stale?: boolean;
  manual_published_at?: string | null;
  published_at?: string | null;
}

export interface Job {
  id: string;
  project_id: string | null;
  user_id: string;
  kind: string;
  status: string;
  stage: string | null;
  progress: number;
  error_code: string | null;
  error_detail: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface JobEvent {
  seq: number;
  level: string;
  message: string;
  progress: number | null;
  stage: string | null;
  data: Record<string, unknown>;
  created_at: string;
}

export interface Upload {
  id: string;
  filename: string;
  size_bytes: number;
  received_bytes: number;
  status: string;
  chunk_size: number;
  created_at: string;
  expires_at: string | null;
}

export interface AdminUser {
  id: string;
  email: string;
  role: string;
  is_active: boolean;
  created_at: string;
  last_login_at: string | null;
  storage_bytes: number;
  quota_bytes: number;
  project_count: number;
  google_connected: boolean;
}

export interface Invite {
  id: string;
  email: string | null;
  created_at: string;
  expires_at: string;
  accepted_at: string | null;
  revoked_at: string | null;
  state: string;
}

export interface AdminJob extends Job {
  user_email: string;
}

export interface Stats {
  users: number;
  projects: number;
  jobs_queued: number;
  jobs_running: number;
  jobs_failed: number;
  disk_total_bytes: number;
  disk_used_bytes: number;
  disk_free_bytes: number;
  quota: {
    date_pacific: string;
    units_used: number;
    units_limit: number;
    uploads_used: number;
    uploads_limit: number;
    user_units_used: number;
    user_uploads_limit: number;
    estimated_uploads_left: number;
  };
}

export interface AdminSettings {
  max_upload_bytes: number;
  max_source_seconds: number;
  user_storage_bytes: number;
  upload_daily_global: number;
  upload_daily_per_user: number;
  default_skip_transcript: boolean;
  max_invited_users: number;
}

export interface SessionInfo {
  id: string;
  created_at: string;
  last_seen_at: string;
  ip: string | null;
  user_agent: string | null;
  current: boolean;
}
