import React, { createContext, useContext, useEffect, useState, useCallback } from "react";
import { checkAuth, getServerUrl, logout as apiLogout } from "./bot-api";

interface AuthContextType {
  isAuthenticated: boolean;
  isLoading: boolean;
  serverUrl: string;
  setAuthenticated: (val: boolean) => void;
  setServerUrl: (url: string) => void;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType>({
  isAuthenticated: false,
  isLoading: true,
  serverUrl: "",
  setAuthenticated: () => {},
  setServerUrl: () => {},
  logout: async () => {},
  refresh: async () => {},
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [isAuthenticated, setAuthenticated] = useState(false);
  const [isLoading, setIsLoading] = useState(true);
  const [serverUrl, setServerUrlState] = useState("");

  const refresh = useCallback(async () => {
    try {
      const url = await getServerUrl();
      setServerUrlState(url);
      if (url) {
        const authed = await checkAuth();
        setAuthenticated(authed);
      } else {
        setAuthenticated(false);
      }
    } catch {
      setAuthenticated(false);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const logout = useCallback(async () => {
    await apiLogout();
    setAuthenticated(false);
  }, []);

  const setServerUrl = useCallback((url: string) => {
    setServerUrlState(url);
  }, []);

  return (
    <AuthContext.Provider
      value={{ isAuthenticated, isLoading, serverUrl, setAuthenticated, setServerUrl, logout, refresh }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}
