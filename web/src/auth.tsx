import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { api, setCsrfToken } from "./api";
import type { AuthPayload } from "./types";

interface AuthContextValue {
  auth: AuthPayload | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  acceptInvite: (token: string, password: string, email?: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function applyAuth(payload: AuthPayload) {
  setCsrfToken(payload.csrf);
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [auth, setAuth] = useState<AuthPayload | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const payload = await api.auth.me();
      applyAuth(payload);
      setAuth(payload);
    } catch {
      setAuth(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    const handleUnauthorized = () => {
      setCsrfToken("");
      setAuth(null);
    };
    window.addEventListener("clipforge:unauthorized", handleUnauthorized);
    return () => window.removeEventListener("clipforge:unauthorized", handleUnauthorized);
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const payload = await api.auth.login(email, password);
    applyAuth(payload);
    setAuth(payload);
  }, []);

  const acceptInvite = useCallback(async (token: string, password: string, email?: string) => {
    const payload = await api.auth.acceptInvite(token, password, email);
    applyAuth(payload);
    setAuth(payload);
  }, []);

  const logout = useCallback(async () => {
    try {
      await api.auth.logout();
    } finally {
      setCsrfToken("");
      setAuth(null);
    }
  }, []);

  const value = useMemo(
    () => ({ auth, loading, login, acceptInvite, logout, refresh }),
    [auth, loading, login, acceptInvite, logout, refresh],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside AuthProvider");
  return value;
}
