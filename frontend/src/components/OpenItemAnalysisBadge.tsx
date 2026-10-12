import { useEffect, useState } from "react";
import { api } from "../api/client";

/** A small header indicator so the user can see from any task that an open item analysis is still running
 *  (its queries can take minutes). Polls cheaply; shows nothing when idle. */
export default function OpenItemAnalysisBadge() {
  const [text, setText] = useState("");

  useEffect(() => {
    let alive = true;
    async function poll() {
      try {
        const s = await api.openItemProgress();
        if (!alive) return;
        setText(s.status === "running" ? `Open items ${s.completed}/${s.total}` : "");
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
