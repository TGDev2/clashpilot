import { useCallback, useEffect, useState } from "react";
import type { ApiProblem, Outcome } from "./client";

export type ApiResource<T> = {
  data: T | undefined;
  problem: ApiProblem | undefined;
  loading: boolean;
  reload: () => void;
};

/** Load once per `key` (and on `reload`). `load` may be inlined: only `key` triggers a fetch. */
export function useApi<T>(load: () => Promise<Outcome<T>>, key: string | null): ApiResource<T> {
  const [state, setState] = useState<{ data?: T; problem?: ApiProblem; loading: boolean }>({
    loading: key !== null,
  });
  const [tick, setTick] = useState(0);
  // biome-ignore lint/correctness/useExhaustiveDependencies: `key` and `tick` are the triggers.
  useEffect(() => {
    if (key === null) {
      setState({ loading: false });
      return;
    }
    let cancelled = false;
    setState((previous) => ({ ...previous, loading: true }));
    void load().then((outcome) => {
      if (cancelled) {
        return;
      }
      setState(
        outcome.ok
          ? { data: outcome.data, loading: false }
          : { problem: outcome.problem, loading: false },
      );
    });
    return () => {
      cancelled = true;
    };
  }, [key, tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data: state.data, problem: state.problem, loading: state.loading, reload };
}
