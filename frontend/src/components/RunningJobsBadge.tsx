import { useEffect, useState } from "react";
import { api } from "../api/client";

/** Header indicators so the user can see from any task that Generate Table List, Find Archiving Objects, Find
 *  Header Tables or a reference-document check is still working in the background (like the analysis tasks'
 *  badges). Polls cheaply; shows nothing when idle. */
export default function RunningJobsBadge() {
  const [labels, setLabels] = useState<string[]>([]);

  useEffect(() => {
    let alive = true;
    async function poll() {
      const checks = await Promise.allSettled([
        api.topTablesProgress(),
        api.db15BatchProgress(),
        api.db15ScoreProgress(),
        api.headerTablesBatchProgress(),
        api.referenceDocProgress(),
        api.headerReferenceProgress(),
      ]);
      if (!alive) return;
      const running = (i: number) => {
        const c = checks[i];
        return c.status === "fulfilled" && c.value.status === "running" ? c.value : null;
      };
      const out: string[] = [];
      if (running(0)) out.push("Table list");
      const lookup = running(1);
      const scoring = running(2);
      if (lookup) out.push(`Archiving objects ${lookup.completed}/${lookup.total}`);
      else if (scoring) out.push(`Scoring ${scoring.completed}/${scoring.total}`);
      const headers = running(3);
      if (headers) out.push(`Header tables ${headers.completed}/${headers.total}`);
      if (running(4) || running(5)) out.push("Reference check");
      setLabels(out);
    }
    poll();
    const timer = setInterval(poll, 5000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  return (
    <>
      {labels.map((text) => (
        <span key={text} className="status-badge working" title="Running in the background — you can keep using other tasks">
          ⏳ {text}
        </span>
      ))}
    </>
  );
}
