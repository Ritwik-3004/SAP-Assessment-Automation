import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { useResultsBinding } from "../assistantContext";
import { ChatIcon, ChevronRight, CloseIcon, DockIcon } from "./icons";

/** docked: beside the page; collapsed: only a floating button; popup: floating over the page. */
export type AssistantMode = "docked" | "collapsed" | "popup";

interface Message {
  role: "user" | "assistant";
  content: string;
}

const SUGGESTIONS = [
  "Which archiving object does the DVM Guide recommend for BKPF?",
  "Does BC_SBAL exist in this SAP system?",
  "Which archiving object has to be archived before FI_DOCUMNT?",
  "Find the housekeeping program for table COSP on SAP for Me",
  "How many rows does BSEG have?",
];

/** The SAP assistant (a docked column, or a floating button and pop-up): answers from the DVM Guide, SAP for Me and the live SAP system, and
 *  (when Find Archiving Objects has results) can discuss and change them. */
export default function AssistantPanel({
  mode,
  onModeChange,
}: {
  mode: AssistantMode;
  onModeChange: (mode: AssistantMode) => void;
}) {
  const results = useResultsBinding();
  const [history, setHistory] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  // The reply while it is being written, and what the assistant is doing while it is not writing (running a tool,
  // waiting for a rate limit)
  const [streamText, setStreamText] = useState("");
  const [status, setStatus] = useState("");
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // A reply that arrives while only the floating button is showing is flagged on the button
  const [unread, setUnread] = useState(false);
  const modeRef = useRef(mode);
  const previousMode = useRef(mode);

  function changeMode(next: AssistantMode) {
    if (next !== "collapsed") setUnread(false);
    onModeChange(next);
  }

  useEffect(() => {
    modeRef.current = mode;
    // Opening the assistant puts the cursor in the question box (not on the first render)
    if (previousMode.current === "collapsed" && mode !== "collapsed") {
      setTimeout(() => textareaRef.current?.focus(), 60);
    }
    previousMode.current = mode;
  }, [mode]);

  useEffect(() => {
    if (mode !== "popup") return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onModeChange("collapsed");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mode, onModeChange]);

  useEffect(() => {
    // while the reply is being written, follow it without the smooth-scroll animation restarting on every word
    bottomRef.current?.scrollIntoView({ behavior: loading ? "auto" : "smooth" });
  }, [history, loading, streamText, status]);

  // Leaving the page (or closing the app) stops the answer being written
  useEffect(() => () => abortRef.current?.abort(), []);

  async function send(raw: string) {
    const msg = raw.trim();
    if (!msg || loading) return;

    const nextHistory: Message[] = [...history, { role: "user", content: msg }];
    setHistory(nextHistory);
    setInput("");
    setLoading(true);
    setError("");
    setStreamText("");
    setStatus("");
    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const res = await api.chatStream(
        msg,
        results?.rows ?? [],
        results?.recommended ?? [],
        history,
        {
          onToken: (text) => {
            setStatus("");
            setStreamText((so_far) => so_far + text);
          },
          onStatus: setStatus,
        },
        controller.signal
      );
      setHistory([...nextHistory, { role: "assistant", content: res.reply }]);
      if (modeRef.current === "collapsed") setUnread(true);
      if (results && res.updated_rows && res.updated_recommended) {
        results.apply(res.updated_rows, res.updated_recommended);
      }
    } catch (err: unknown) {
      if (controller.signal.aborted) return;
      setError(err instanceof Error ? err.message : "Chat failed");
      setHistory(history);
      setInput(msg);
    } finally {
      setStreamText("");
      setStatus("");
      setLoading(false);
      setTimeout(() => textareaRef.current?.focus(), 50);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  }

  const tableCount = results ? new Set(results.rows.map((r) => r["Table Name"])).size : 0;

  return (
    <>
    <aside className={`assistant mode-${mode}`} aria-label="SAP assistant" inert={mode === "collapsed"}>
      <div className="assistant-header">
        <div>
          <span className="assistant-title">
            <span className="assistant-title-icon">
              <ChatIcon />
            </span>
            SAP Assistant
          </span>
          <span className="assistant-sub">DVM Guide · SAP for Me · live SAP system</span>
        </div>
        <div className="assistant-actions">
          {history.length > 0 && (
            <button
              className="btn-link"
              onClick={() => {
                setHistory([]);
                setError("");
              }}
              disabled={loading}
            >
              Clear
            </button>
          )}
          {mode === "popup" && (
            <button
              type="button"
              className="icon-btn"
              onClick={() => changeMode("docked")}
              aria-label="Dock the assistant beside the page"
              title="Dock beside the page"
            >
              <DockIcon />
            </button>
          )}
          <button
            type="button"
            className="icon-btn"
            onClick={() => changeMode("collapsed")}
            aria-label={mode === "popup" ? "Close the assistant" : "Collapse the assistant"}
            title={mode === "popup" ? "Close" : "Collapse"}
          >
            {mode === "popup" ? <CloseIcon /> : <ChevronRight />}
          </button>
        </div>
      </div>

      <div className="assistant-context">
        {results
          ? `Results loaded: ${tableCount} table${tableCount !== 1 ? "s" : ""} — ask about them or ask me to change one.`
          : "No scored results yet — I can still answer from the DVM Guide, SAP for Me and your SAP system."}
      </div>

      <div className="assistant-messages">
        {history.length === 0 && (
          <div className="chat-empty">
            <p className="chat-placeholder">
              Ask about tables, archiving objects, housekeeping programs or your SAP system. I never read what
              is inside a table, only how many entries it has, its size and its structure. I say which source
              each answer came from. Try:
            </p>
            <div className="chat-suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s} className="chat-suggestion" onClick={() => send(s)} disabled={loading}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {history.map((msg, i) => (
          <div key={i} className={`chat-message ${msg.role}`}>
            <div className="chat-bubble">{msg.content}</div>
          </div>
        ))}

        {loading && (
          <div className="chat-message assistant">
            {streamText ? (
              <div className="chat-bubble chat-streaming">
                {streamText.trimEnd()}
                <span className="chat-caret" aria-hidden="true" />
              </div>
            ) : (
              <div className="chat-bubble chat-thinking">
                <span className="chat-dot" />
                <span className="chat-dot" />
                <span className="chat-dot" />
                {status && <span className="chat-status-inline">{status}</span>}
              </div>
            )}
          </div>
        )}
        {loading && streamText && status && <p className="chat-status">{status}</p>}
        <div ref={bottomRef} />
      </div>

      {error && <p className="chat-error">{error}</p>}

      <form
        className="chat-input-row"
        onSubmit={(e) => {
          e.preventDefault();
          send(input);
        }}
      >
        <textarea
          ref={textareaRef}
          className="chat-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask anything… (Enter to send)"
          rows={2}
          disabled={loading}
        />
        <button type="submit" className="btn btn-primary chat-send" disabled={!input.trim() || loading}>
          Send
        </button>
      </form>
      <p className="assistant-note">
        Table contents are never read or shared. Please don't paste client data here: your messages go to the AI model.
      </p>
    </aside>

    {mode === "collapsed" && (
      <button type="button" className="assistant-fab" onClick={() => changeMode("popup")}>
        <ChatIcon />
        Ask the assistant
        {unread && <span className="assistant-fab-dot" aria-label="New reply" />}
      </button>
    )}
    </>
  );
}
