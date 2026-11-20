import type {
  AdminJob,
  AdminSettings,
  AdminUser,
  AuthPayload,
  Clip,
  Invite,
  Job,
  Moment,
  Project,
  SessionInfo,
  Stats,
  Timeline,
  Upload,
  User,
} from "./types";

let csrfToken = "";

export function setCsrfToken(token: string) {
  csrfToken = token;
}

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  const method = (init.method || "GET").toUpperCase();
  if (method !== "GET" && method !== "HEAD" && csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  if (typeof init.body === "string" && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(path, {
    credentials: "include",
    ...init,
    headers,
  });
  if (!response.ok) {
    if (response.status === 401) {
      window.dispatchEvent(new Event("clipforge:unauthorized"));
    }
    let detail = response.statusText;
    try {
      const data = await response.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail ?? data);
    } catch {
      // keep status text
    }
    throw new ApiError(detail, response.status);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  const contentType = response.headers.get("content-type") || "";
  if (contentType.includes("application/json")) {
    return (await response.json()) as T;
  }
  return (await response.text()) as unknown as T;
}

function json(data: unknown): string {
  return JSON.stringify(data);
}

export const api = {
  auth: {
    me: () => request<AuthPayload>("/api/v1/auth/me"),
    login: (email: string, password: string) =>
      request<AuthPayload>("/api/v1/auth/login", { method: "POST", body: json({ email, password }) }),
    logout: () => request<{ ok: boolean }>("/api/v1/auth/logout", { method: "POST" }),
    acceptInvite: (token: string, password: string, email?: string) =>
      request<AuthPayload>("/api/v1/auth/accept-invite", {
        method: "POST",
        body: json({ token, password, email: email || null }),
      }),
    forgotPassword: (email: string) =>
      request<{ ok: boolean; message: string; dev_reset_url?: string }>("/api/v1/auth/forgot-password", {
        method: "POST",
        body: json({ email }),
      }),
    resetPassword: (token: string, password: string) =>
      request<{ ok: boolean }>("/api/v1/auth/reset-password", {
        method: "POST",
        body: json({ token, password }),
      }),
    changePassword: (current_password: string, new_password: string) =>
      request<{ ok: boolean }>("/api/v1/auth/change-password", {
        method: "POST",
        body: json({ current_password, new_password }),
      }),
    sessions: () => request<{ sessions: SessionInfo[] }>("/api/v1/auth/sessions"),
    deleteSession: (id: string) =>
      request<{ ok: boolean }>(`/api/v1/auth/sessions/${encodeURIComponent(id)}`, { method: "DELETE" }),
  },
  projects: {
    list: () =>
      request<{ projects: Project[]; storage: { used_bytes: number; quota_bytes: number } }>("/api/v1/projects"),
    create: (body: {
      youtube_url?: string;
      upload_id?: string;
      title?: string;
      options?: Record<string, unknown>;
    }) => request<{ project: Project; job: Job }>("/api/v1/projects", { method: "POST", body: json(body) }),
    get: (id: string) => request<Project>(`/api/v1/projects/${id}`),
    delete: (id: string) => request<{ ok: boolean }>(`/api/v1/projects/${id}`, { method: "DELETE" }),
    timeline: (id: string) => request<Timeline>(`/api/v1/projects/${id}/timeline`),
    analyze: (id: string) => request<{ job: Job }>(`/api/v1/projects/${id}/analyze`, { method: "POST" }),
    patchMoments: (id: string, moments: Moment[]) =>
      request<{ moments: Moment[] }>(`/api/v1/projects/${id}/moments`, {
        method: "PATCH",
        body: json({ moments }),
      }),
    render: (id: string, moment_ids?: string[]) =>
      request<{ job: Job }>(`/api/v1/projects/${id}/render`, {
        method: "POST",
        body: json({ moment_ids: moment_ids || null }),
      }),
    cleanup: (id: string) =>
      request<{ ok: boolean; freed_bytes: number }>(`/api/v1/projects/${id}/cleanup`, { method: "POST" }),
    clips: (id: string) => request<{ clips: Clip[] }>(`/api/v1/projects/${id}/clips`),
    manifestUrl: (id: string) => `/api/v1/projects/${id}/manifest`,
  },
  uploads: {
    init: (filename: string, size: number) =>
      request<Upload>("/api/v1/uploads", { method: "POST", body: json({ filename, size }) }),
    get: (id: string) => request<Upload>(`/api/v1/uploads/${id}`),
    putChunk: (id: string, offset: number, chunk: Blob) =>
      request<Upload>(`/api/v1/uploads/${id}?offset=${offset}`, { method: "PUT", body: chunk }),
    complete: (id: string) => request<Upload>(`/api/v1/uploads/${id}/complete`, { method: "POST" }),
    remove: (id: string) => request<{ ok: boolean }>(`/api/v1/uploads/${id}`, { method: "DELETE" }),
  },
  jobs: {
    get: (id: string) => request<Job>(`/api/v1/jobs/${id}`),
    cancel: (id: string) => request<{ ok: boolean }>(`/api/v1/jobs/${id}/cancel`, { method: "POST" }),
    eventsUrl: (id: string) => `/api/v1/jobs/${id}/events`,
  },
  clips: {
    patch: (
      projectId: string,
      clipId: string,
      body: { overlay?: string; suggested_title?: string; suggested_description?: string; tags?: string[] },
    ) =>
      request<{ clip: Clip }>(`/api/v1/projects/${projectId}/clips/${encodeURIComponent(clipId)}`, {
        method: "PATCH",
        body: json(body),
      }),
    rerender: (projectId: string, clipId: string) =>
      request<{ job: Job }>(
        `/api/v1/projects/${projectId}/clips/${encodeURIComponent(clipId)}/rerender`,
        { method: "POST" },
      ),
    upload: (projectId: string, clip_ids: string[], privacy: string) =>
      request<{ jobs: Job[]; quota: unknown; manual_fallback: { message: string } }>(
        `/api/v1/projects/${projectId}/upload`,
        { method: "POST", body: json({ clip_ids, privacy }) },
      ),
    markManual: (projectId: string, clipId: string) =>
      request<{ clip: Clip }>(
        `/api/v1/projects/${projectId}/clips/${encodeURIComponent(clipId)}/manual`,
        { method: "POST" },
      ),
  },
  oauth: {
    start: () => request<{ url: string }>("/api/v1/oauth/google/start"),
    disconnect: () => request<{ ok: boolean }>("/api/v1/oauth/google/disconnect", { method: "POST" }),
  },
  admin: {
    users: () => request<{ users: AdminUser[] }>("/api/v1/admin/users"),
    patchUser: (
      id: string,
      body: { is_active?: boolean; quota_bytes?: number; upload_daily_cap?: number },
    ) => request<{ ok: boolean }>(`/api/v1/admin/users/${id}`, { method: "PATCH", body: json(body) }),
    resetPassword: (id: string) =>
      request<{ ok: boolean; reset_url?: string }>(`/api/v1/admin/users/${id}/reset-password`, {
        method: "POST",
      }),
    revokeSessions: (id: string) =>
      request<{ ok: boolean }>(`/api/v1/admin/users/${id}/sessions`, { method: "DELETE" }),
    invites: () => request<{ invites: Invite[] }>("/api/v1/admin/invites"),
    createInvite: (email?: string) =>
      request<{ invite: Invite; invite_url: string; email: { status: string } }>("/api/v1/admin/invites", {
        method: "POST",
        body: json({ email: email || null }),
      }),
    revokeInvite: (id: string) =>
      request<{ ok: boolean }>(`/api/v1/admin/invites/${id}`, { method: "DELETE" }),
    resendInvite: (id: string) =>
      request<{ invite: Invite; invite_url: string }>(`/api/v1/admin/invites/${id}/resend`, {
        method: "POST",
      }),
    jobs: (status?: string) =>
      request<{ jobs: AdminJob[] }>(`/api/v1/admin/jobs${status ? `?status=${encodeURIComponent(status)}` : ""}`),
    cancelJob: (id: string) =>
      request<{ ok: boolean }>(`/api/v1/admin/jobs/${id}/cancel`, { method: "POST" }),
    retryJob: (id: string) =>
      request<{ ok: boolean }>(`/api/v1/admin/jobs/${id}/retry`, { method: "POST" }),
    stats: () => request<Stats>("/api/v1/admin/stats"),
    errors: () => request<{ errors: Array<Record<string, unknown>> }>("/api/v1/admin/errors"),
    settings: () => request<{ settings: AdminSettings }>("/api/v1/admin/settings"),
    patchSettings: (body: Partial<AdminSettings>) =>
      request<{ ok: boolean; applied: Record<string, unknown> }>("/api/v1/admin/settings", {
        method: "PATCH",
        body: json(body),
      }),
  },
};

export async function copyText(value: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    return false;
  }
}

export function formatBytes(value: number): string {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(units.length - 1, Math.floor(Math.log(value) / Math.log(1024)));
  return `${(value / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

export function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
    : `${m}:${String(s).padStart(2, "0")}`;
}

export type { User };
