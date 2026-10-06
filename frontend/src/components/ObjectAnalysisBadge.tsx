import { useEffect, useState } from "react";
import { api } from "../api/client";

/** A small header indicator so the user can see from any task that an archiving object analysis (or its
 *  background SAP for Me step) is still working. Polls cheaply; shows nothing when idle. */
export default function ObjectAnalysisBadge() {
  const [text, setText] = useState("");

  useEffect(() => {
    let alive = true;
    async function poll() {
      try {
        const s = await api.objectAnalysisProgress();
        if (!alive) return;
        if (s.status === "running") setText(`Object analysis ${s.completed}/${s.total}`);
        else if (s.sap_for_me?.status === "running") setText(`SAP for Me ${s.sap_for_me.completed}/${s.sap_for_me.total}`);
        else setText("");
      } catch {
        if (alive) setText("");
      }
    }
    poll();
    const timer = setInterval(poll, 5000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);

  if (!text) return null;
  return (
    <span className="status-badge working" title="Running in the background — you can keep using other tasks">
      ⏳ {text}
    </span>
  );
}
