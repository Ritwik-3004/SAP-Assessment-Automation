import { useMemo, useRef } from "react";

/**
 * Lets a screen stop the polls it started when the user leaves it. The work itself goes on in the backend and the
 * screen picks it up again when it is opened (like the analysis tasks); only the waiting loop of the old screen must
 * end, and must not write into a screen that is gone.
 */
export function useScreenEpoch() {
  const ref = useRef(0);
  return useMemo(
    () => ({
      /** The screen was opened: returns a check that turns false once the screen is left. */
      begin() {
        const mine = ++ref.current;
        return () => ref.current === mine;
      },
      /** A check bound to the current opening, for an action the user starts while the screen is open. */
      current() {
        const mine = ref.current;
        return () => ref.current === mine;
      },
      /** The screen was left. */
      leave() {
        ref.current++;
      },
    }),
    []
  );
}
