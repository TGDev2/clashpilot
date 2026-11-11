import { createContext, type ReactNode, useCallback, useContext, useMemo, useState } from "react";

const STORAGE_KEY = "clashpilot.village";

type VillageContextValue = {
  villageId: string | null;
  setVillageId: (id: string) => void;
};

const VillageContext = createContext<VillageContextValue | null>(null);

function read(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

/** The current village, kept in localStorage so a reload finds it again. */
export function VillageProvider({ children }: { children: ReactNode }) {
  const [villageId, setId] = useState<string | null>(read);
  const setVillageId = useCallback((id: string) => {
    setId(id);
    try {
      localStorage.setItem(STORAGE_KEY, id);
    } catch {
      // Private mode: the village then lives for this session only.
    }
  }, []);
  const value = useMemo(() => ({ villageId, setVillageId }), [villageId, setVillageId]);
  return <VillageContext.Provider value={value}>{children}</VillageContext.Provider>;
}

export function useVillage(): VillageContextValue {
  const value = useContext(VillageContext);
  if (value === null) {
    throw new Error("useVillage must be used inside VillageProvider");
  }
  return value;
}
