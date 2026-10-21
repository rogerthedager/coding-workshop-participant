import { createContext, useContext, useEffect, useState } from 'react';
import { api, refreshSession, setSessionListener } from './api';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setSessionListener(setUser);
    // Restore the session from the refresh cookie, if there is one.
    refreshSession()
      .catch(() => {})
      .finally(() => setReady(true));
  }, []);

  const roles = user?.roles || [];
  const value = {
    user,
    ready,
    isAdmin: roles.includes('admin'),
    isEngineer: roles.includes('engineer'),
    isLead: roles.includes('lead'),
    // Staff can see the engineer directory; a lead alone cannot.
    isStaff: roles.includes('admin') || roles.includes('engineer'),
    /** Refresh the signed-in user (e.g. after saving a default seat). */
    refreshUser: () => api.me().then(setUser),
    login: api.login,
    logout: api.logout,
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth() {
  return useContext(AuthContext);
}
