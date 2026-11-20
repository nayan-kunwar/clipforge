import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { formatBytes } from "../api";
import { useAuth } from "../auth";

function titleFor(pathname: string): string {
  if (pathname.startsWith("/admin")) return "Admin";
  if (pathname.startsWith("/settings")) return "Settings";
  if (pathname.startsWith("/new")) return "New project";
  if (pathname.startsWith("/p/") && pathname.endsWith("/clips")) return "Clips";
  if (pathname.startsWith("/p/")) return "Project";
  return "Library";
}

export function Layout() {
  const { auth, logout } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const quota = auth?.quota;
  const used = quota?.used_bytes ?? 0;
  const total = quota?.quota_bytes ?? 1;
  const percent = Math.min(100, Math.round((used / Math.max(1, total)) * 100));

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">C</span>
          <span>ClipForge</span>
        </div>
        <nav className="nav">
          <NavLink to="/" end>
            Library
          </NavLink>
          <NavLink to="/new">New project</NavLink>
          <NavLink to="/settings">Settings</NavLink>
          {auth?.user.role === "admin" ? <NavLink to="/admin">Admin</NavLink> : null}
        </nav>
        <div className="sidebar-footer">
          <div>
            <div className="small muted">Storage</div>
            <div className="progress quota-bar">
              <div style={{ width: `${percent}%` }} />
            </div>
            <div className="small faint" style={{ marginTop: 4 }}>
              {formatBytes(used)} / {formatBytes(total)}
            </div>
          </div>
          <div className="small" style={{ overflow: "hidden", textOverflow: "ellipsis" }}>
            {auth?.user.email}
          </div>
          <button
            className="btn btn-sm"
            onClick={() => {
              void logout().then(() => navigate("/login"));
            }}
          >
            Sign out
          </button>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <div>
            <strong>{titleFor(location.pathname)}</strong>
          </div>
          <div className="row small muted">
            <span className="badge blue">local-first</span>
            <span>{auth?.user.email}</span>
          </div>
        </header>
        <div className="content">
          <Outlet />
        </div>
      </main>
    </div>
  );
}
