import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

// The company name and logo come from the backend, so the owner can change them without anyone touching code.
// They are public (the sign-in page needs them), so this reads them without signing in. If the server cannot be
// reached the page still works and just shows the plain name.
const FALLBACK = { name: "Nisa Local", logoUrl: null };
const Context = createContext({ ...FALLBACK, reload: () => {} });

export function BrandingProvider({ children }) {
  const [brand, setBrand] = useState(FALLBACK);
  const reload = useCallback(async () => {
    try {
      const response = await fetch("/api/branding/");
      if (!response.ok) return;
      const data = await response.json();
      setBrand({ name: data.company_name || FALLBACK.name, logoUrl: data.has_logo ? `/api/branding/logo/?v=${data.version}` : null });
    } catch {
      /* keep what is showing */
    }
  }, []);
  useEffect(() => { reload(); }, [reload]);
  useEffect(() => { document.title = `${brand.name} staff portal`; }, [brand.name]);
  const value = useMemo(() => ({ ...brand, reload }), [brand, reload]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export const useBranding = () => useContext(Context);

// The logo if there is one, otherwise the name. The logo sits on a light chip so any logo colours stay readable.
export function Brand({ className = "brand" }) {
  const { name, logoUrl } = useBranding();
  return logoUrl ? (
    <span className={`${className} has-logo`}>
      <img src={logoUrl} alt={name} />
    </span>
  ) : (
    <span className={className}>{name}</span>
  );
}
