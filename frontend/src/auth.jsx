import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api, resume, setSignedOutHandler, signIn, signOut } from "./api";

const AuthContext = createContext(null);
export const useAuth = () => useContext(AuthContext);

export const ROLE_NAMES = { owner: "Owner", hr: "HR", manager: "Store manager", employee: "Employee" };

export function AuthProvider({ children }) {
  const [state, setState] = useState({ status: "loading", user: null });

  const loadUser = useCallback(async () => {
    const user = await api("/auth/me/");
    setState({ status: "in", user });
  }, []);

  useEffect(() => {
    setSignedOutHandler(() => setState({ status: "out", user: null }));
    (async () => {
      try {
        if (await resume()) await loadUser();
        else setState({ status: "out", user: null });
      } catch {
        setState({ status: "out", user: null });
      }
    })();
  }, [loadUser]);

  const value = useMemo(
    () => ({
      ...state,
      async login(email, password) {
        await signIn(email, password);
        await loadUser();
      },
      logout() {
        signOut();
        setState({ status: "out", user: null });
      },
    }),
    [state, loadUser],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
