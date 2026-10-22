import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api, type HouseholdState } from "./api";

type Ctx = {
  state: HouseholdState | null;
  error: string | null;
  refresh: () => Promise<void>;
  helperName: (id: string) => string;
  askAssistant: (text: string) => void;
  setAskAssistant: (fn: (text: string) => void) => void;
};

const HouseholdContext = createContext<Ctx | null>(null);

export function HouseholdProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<HouseholdState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ask, setAsk] = useState<{ fn: (t: string) => void }>({ fn: () => {} });

  const refresh = useCallback(async () => {
    try {
      setState(await api<HouseholdState>("GET", "/api/state"));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const helperName = useCallback(
    (id: string) => state?.helpers.find((h) => h.helper_id === id)?.name ?? id,
    [state],
  );

  return (
    <HouseholdContext.Provider
      value={{
        state,
        error,
        refresh,
        helperName,
        askAssistant: (t) => ask.fn(t),
        setAskAssistant: useCallback((fn) => setAsk({ fn }), []),
      }}
    >
      {children}
    </HouseholdContext.Provider>
  );
}

export function useHousehold() {
  const c = useContext(HouseholdContext);
  if (!c) throw new Error("useHousehold outside provider");
  return c;
}
