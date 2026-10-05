import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { useResultsBinding } from "../assistantContext";

interface Message {
  role: "user" | "assistant";
  content: string;
}

const SUGGESTIONS = [
  "Which archiving object does the DVM Guide recommend for BKPF?",
  "Does BC_SBAL exist in this SAP system?",
  "What does transaction SARA do?",
  "Find the housekeeping program for table COSP on SAP for Me",
  "How many rows does BSEG have?",
];

/** The always-visible assistant: answers from the DVM Guide, SAP for Me and the live SAP system, and
 *  (when Find Archiving Objects has results) can discuss and change them. */
export default function AssistantPanel() {
  const results = useResultsBinding();
  const [history, setHistory] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [history, loading]);

  async function send(raw: string) {
    const msg = raw.trim();
    if (!msg || loading) return;

    const nextHistory: Message[] = [...history, { role: "user", content: msg }];
    setHistory(nextHistory);
    setInput("");
    setLoading(true);
    setError("");

    try {
      const res = await api.chat(msg, results?.rows ?? [], results?.recommended ?? [], history);
      setHistory([...nextHistory, { role: "assistant", content: res.reply }]);
      if (results && res.updated_rows && res.updated_recommended) {
        results.apply(res.updated_rows, res.updated_recommended);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Chat failed");
      setHistory(history);
      setInput(msg);
    } finally {
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
    <aside className="assistant" aria-label="SAP assistant">
      <div className="assistant-header">
        <div>
          <span className="assistant-title">💬 SAP Assistant</span>
          <span className="assistant-sub">DVM Guide · SAP for Me · live SAP system</span>
        </div>
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
              Ask about tables, archiving objects, housekeeping programs or SAP transactions. I say which
              source each answer came from. Try:
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
            <div className="chat-bubble chat-thinking">
              <span className="chat-dot" />
              <span className="chat-dot" />
              <span className="chat-dot" />
            </div>
          </div>
        )}
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
    </aside>
  );
}
