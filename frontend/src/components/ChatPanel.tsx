import { useState, useRef, useEffect } from "react";
import { api } from "../api/client";
import type { ScoredResult } from "../types";

interface Message {
  role: "user" | "assistant";
  content: string;
}

interface Props {
  scored: ScoredResult;
}

const SUGGESTIONS = [
  "Check what archiving objects BALDAT has",
  "Why did the top table score high?",
  "Are there any tables with no archiving object found?",
];

export default function ChatPanel({ scored }: Props) {
  const [history, setHistory] = useState<Message[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [history, loading]);

  async function send(msg: string) {
    msg = msg.trim();
    if (!msg || loading) return;

    const userMsg: Message = { role: "user", content: msg };
    const nextHistory = [...history, userMsg];
    setHistory(nextHistory);
    setInput("");
    setLoading(true);
    setError("");

    try {
      const res = await api.chat(
        msg,
        scored.rows ?? [],
        scored.recommended ?? [],
        history
      );
      setHistory([...nextHistory, { role: "assistant", content: res.reply }]);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Chat failed");
      setHistory(history);
    } finally {
      setLoading(false);
      setTimeout(() => textareaRef.current?.focus(), 50);
    }
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    send(input);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  }

  return (
    <div className="chat-panel">
      <div className="chat-header">
        <span className="chat-icon">💬</span>
        <span>Ask about these results</span>
        <span className="chat-subheading">
          Ask follow-up questions or check live SAP data
        </span>
      </div>

      <div className="chat-messages">
        {history.length === 0 && (
          <div className="chat-empty">
            <p className="chat-placeholder">
              Ask a question about the scored results, or verify data directly
              from SAP. For example:
            </p>
            <div className="chat-suggestions">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  className="chat-suggestion"
                  onClick={() => send(s)}
                  disabled={loading}
                >
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

      <form className="chat-input-row" onSubmit={handleSubmit}>
        <textarea
          ref={textareaRef}
          className="chat-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Ask a question… (Enter to send, Shift+Enter for new line)"
          rows={2}
          disabled={loading}
        />
        <button
          type="submit"
          className="btn btn-primary chat-send"
          disabled={!input.trim() || loading}
        >
          Send
        </button>
      </form>
    </div>
  );
}
