import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

type Row = Record<string, string>;

/** The current scored results, published by the Find Archiving Objects panel so the always-visible
 *  assistant can discuss them and change them (the preview refreshes through *apply*). */
export interface ResultsBinding {
  rows: Row[];
  recommended: Row[];
  apply: (rows: Row[], recommended: Row[]) => void;
}

type SetBinding = (binding: ResultsBinding | null) => void;

const ValueContext = createContext<ResultsBinding | null>(null);
const SetterContext = createContext<SetBinding>(() => {});

export function AssistantProvider({ children }: { children: ReactNode }) {
  const [binding, setBinding] = useState<ResultsBinding | null>(null);
  // The setter context never changes, so panels that publish results don't re-render when the
  // assistant reads them.
  const setter = useMemo<SetBinding>(() => setBinding, []);
  return (
    <SetterContext.Provider value={setter}>
      <ValueContext.Provider value={binding}>{children}</ValueContext.Provider>
    </SetterContext.Provider>
  );
}

/** What the assistant currently knows about the user's results (null before scoring). */
export function useResultsBinding(): ResultsBinding | null {
  return useContext(ValueContext);
}

/** Publish (or clear, with null) the results the assistant can see. Cleared on unmount. */
export function usePublishResults(binding: ResultsBinding | null) {
  const set = useContext(SetterContext);
  const rows = binding?.rows;
  const recommended = binding?.recommended;
  const apply = binding?.apply;
  useEffect(() => {
    set(rows && apply ? { rows, recommended: recommended ?? [], apply } : null);
  }, [set, rows, recommended, apply]);
  useEffect(() => () => set(null), [set]);
}
