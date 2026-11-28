import { createContext, type ReactNode, useCallback, useContext, useMemo, useState } from "react";
import type { Base } from "../api/client";
import { fr, frBuilder } from "../i18n/fr";

const STORAGE_KEY = "clashpilot.village";
const BASE_STORAGE_KEY = "clashpilot.base";

type VillageContextValue = {
  villageId: string | null;
  setVillageId: (id: string) => void;
  /** Which of its two bases the pages show: the home village or the builder base. */
  base: Base;
  setBase: (base: Base) => void;
};

const VillageContext = createContext<VillageContextValue | null>(null);

function read(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function write(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Private mode: the choice then lives for this session only.
  }
}

/** The home village unless the builder base was chosen. */
function readBase(): Base {
  return read(BASE_STORAGE_KEY) === "builder" ? "builder" : "home";
}

/** The current village and which of its bases is shown, kept in localStorage across reloads. */
export function VillageProvider({ children }: { children: ReactNode }) {
  const [villageId, setId] = useState<string | null>(() => read(STORAGE_KEY));
  const setVillageId = useCallback((id: string) => {
    setId(id);
    write(STORAGE_KEY, id);
  }, []);
  const [base, setChosenBase] = useState<Base>(readBase);
  const setBase = useCallback((chosen: Base) => {
    setChosenBase(chosen);
    write(BASE_STORAGE_KEY, chosen);
  }, []);
  const value = useMemo(
    () => ({ villageId, setVillageId, base, setBase }),
    [villageId, setVillageId, base, setBase],
  );
  return <VillageContext.Provider value={value}>{children}</VillageContext.Provider>;
}

export function useVillage(): VillageContextValue {
  const value = useContext(VillageContext);
  if (value === null) {
    throw new Error("useVillage must be used inside VillageProvider");
  }
  return value;
}

/** The labels of the chosen base: the builder base has its own town hall and laboratory. */
export function useLabels() {
  return useVillage().base === "builder" ? frBuilder : fr;
}
