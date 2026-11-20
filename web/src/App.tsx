import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import type { ReactNode } from "react";

import { AuthProvider, useAuth } from "./auth";
import { Layout } from "./components/Layout";
import { AdminPage } from "./pages/Admin";
import { ClipsPage } from "./pages/Clips";
import { ForgotPasswordPage } from "./pages/ForgotPassword";
import { InvitePage } from "./pages/Invite";
import { LibraryPage } from "./pages/Library";
import { LoginPage } from "./pages/Login";
import { NewProjectPage } from "./pages/NewProject";
import { ProjectPage } from "./pages/Project";
import { ResetPasswordPage } from "./pages/ResetPassword";
import { SettingsPage } from "./pages/Settings";

function Protected({ children }: { children: ReactNode }) {
  const { auth, loading } = useAuth();
  const location = useLocation();
  if (loading) {
    return (
      <div className="auth-page">
        <div className="auth-card">
          <div className="brand">
            <span className="brand-mark">C</span>
            <span>ClipForge</span>
          </div>
          <div className="muted">Loading…</div>
        </div>
      </div>
    );
  }
  if (!auth) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <>{children}</>;
}

function AdminOnly({ children }: { children: ReactNode }) {
  const { auth } = useAuth();
  if (!auth || auth.user.role !== "admin") return <Navigate to="/" replace />;
  return <>{children}</>;
}

export function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/forgot-password" element={<ForgotPasswordPage />} />
          <Route path="/reset-password" element={<ResetPasswordPage />} />
          <Route path="/invite/:token" element={<InvitePage />} />
          <Route
            element={
              <Protected>
                <Layout />
              </Protected>
            }
          >
            <Route path="/" element={<LibraryPage />} />
            <Route path="/new" element={<NewProjectPage />} />
            <Route path="/p/:id" element={<ProjectPage />} />
            <Route path="/p/:id/clips" element={<ClipsPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route
              path="/admin"
              element={
                <AdminOnly>
                  <AdminPage />
                </AdminOnly>
              }
            />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </BrowserRouter>
  );
}
