import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import type { LlmSettings, LlmTestResult, LlmUsage } from "../types";

interface Props {
  onChanged: (label: string) => void;
}

function UsageBar({ used, limit }: { used: number; limit: number }) {
  const pct = Math.min(100, Math.round((used / limit) * 100));
  return (
    <div className="usage-bar" title={`${pct}% used`}>
      <div className={`usage-fill${pct >= 80 ? " high" : ""}`} style={{ width: `${pct}%` }} />
    </div>
  );
}

export default function ModelSettingsPanel({ onChanged }: Props) {
  const [settings, setSettings] = useState<LlmSettings | null>(null);
  const [provider, setProvider] = useState<"anthropic" | "groq">("anthropic");
  const [model, setModel] = useState("");
  const [usage, setUsage] = useState<LlmUsage | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [testResult, setTestResult] = useState<LlmTestResult | null>(null);

  const applySettings = useCallback(
    (s: LlmSettings) => {
      setSettings(s);
      setProvider(s.provider);
      setModel(s.provider === "groq" ? s.model : s.groq_models[0]?.id ?? "");
      onChanged(s.label);
    },
    [onChanged]
  );

  const refreshUsage = useCallback(() => {
    api.getLlmUsage().then(setUsage).catch(() => {});
  }, []);

  useEffect(() => {
    api.getLlmSettings().then(applySettings).catch(() => {});
    refreshUsage();
    const timer = setInterval(refreshUsage, 20000);
    return () => clearInterval(timer);
  }, [applySettings, refreshUsage]);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError("");
    setNotice("");
    setTestResult(null);
    try {
      const saved = await api.saveLlmSettings({
        provider,
        model: provider === "groq" ? model : "",
      });
      applySettings(saved);
      refreshUsage();
      setNotice(`Saved. Every AI step now uses ${saved.label}.`);
      setTimeout(() => setNotice(""), 5000);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not save");
    } finally {
      setSaving(false);
    }
  }

  async function handleTest() {
    setTesting(true);
    setTestResult(null);
    setError("");
    try {
      setTestResult(await api.testLlm());
      refreshUsage();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Test failed");
    } finally {
      setTesting(false);
    }
  }

  if (!settings) {
    return null;
  }

  const dirty = provider !== settings.provider || (provider === "groq" && model !== settings.model);
  const limits = usage?.limits;

  return (
    <div className="login-panel">
      <h2 className="panel-title">AI Model</h2>
      <form className="login-form" onSubmit={handleSave}>
        <div className="form-row">
          <label>Provider</label>
          <select value={provider} onChange={(e) => setProvider(e.target.value as "anthropic" | "groq")}>
            <option value="anthropic">Claude ({settings.claude_model})</option>
            <option value="groq">Groq (free tier)</option>
          </select>
        </div>

        {provider === "anthropic" && !settings.claude_key_configured && (
          <p className="form-error">ANTHROPIC_API_KEY is not set in backend/.env.</p>
        )}

        {provider === "groq" && (
          <>
            <div className="form-row">
              <label>Groq model</label>
              <select value={model} onChange={(e) => setModel(e.target.value)}>
                {settings.groq_models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.label}
                  </option>
                ))}
              </select>
            </div>
            {!settings.groq_key_configured && (
              <p className="form-error">
                GROQ_API_KEY is not set. Add it to backend/.env and restart the backend.
              </p>
            )}
            <p className="model-note">
              Free tier: {settings.groq_limits.rpm} requests/min, {settings.groq_limits.tpm.toLocaleString()}{" "}
              tokens/min, {settings.groq_limits.rpd.toLocaleString()} requests and{" "}
              {settings.groq_limits.tpd.toLocaleString()} tokens/day. Large batches run slowly and may not
              finish in a day.
            </p>
          </>
        )}

        {error && <p className="form-error">{error}</p>}

        <div className="login-actions">
          <button type="submit" className="btn btn-secondary" disabled={saving || !dirty}>
            {saving ? "Saving…" : "Save"}
          </button>
          <button type="button" className="btn btn-secondary" onClick={handleTest} disabled={testing || dirty}>
            {testing ? "Testing…" : "Test"}
          </button>
        </div>

        {notice && <p className="saved-notice">{notice}</p>}
        {testResult &&
          (testResult.ok ? (
            <p className="saved-notice">
              {testResult.label} replied in {testResult.seconds}s.
            </p>
          ) : (
            <p className="form-error">{testResult.message}</p>
          ))}

        {usage && limits && usage.provider === "groq" && (
          <div className="usage-meter">
            <div className="model-note">Today ({usage.model})</div>
            <div className="model-note">
              {usage.requests.toLocaleString()} / {limits.rpd.toLocaleString()} requests
            </div>
            <UsageBar used={usage.requests} limit={limits.rpd} />
            <div className="model-note">
              {usage.tokens.toLocaleString()} / {limits.tpd.toLocaleString()} tokens
            </div>
            <UsageBar used={usage.tokens} limit={limits.tpd} />
          </div>
        )}
      </form>
    </div>
  );
}
