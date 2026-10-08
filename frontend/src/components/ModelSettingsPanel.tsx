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
  const [provider, setProvider] = useState<"anthropic" | "groq" | "ollama">("anthropic");
  const [model, setModel] = useState("");
  const [ollamaModel, setOllamaModel] = useState(""); // "" = Auto (best installed)
  const [refreshing, setRefreshing] = useState(false);
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
      setOllamaModel(s.ollama_model);
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
        model: provider === "groq" ? model : provider === "ollama" ? ollamaModel : "",
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

  async function handleRefreshOllama() {
    setRefreshing(true);
    setError("");
    try {
      const fresh = await api.getLlmSettings(true);
      setSettings(fresh);
      if (ollamaModel && !fresh.ollama.models.some((m) => m.id === ollamaModel)) setOllamaModel("");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not refresh");
    } finally {
      setRefreshing(false);
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

  const dirty =
    provider !== settings.provider ||
    (provider === "groq" && model !== settings.model) ||
    (provider === "ollama" && ollamaModel !== settings.ollama_model);
  const ollama = settings.ollama;
  const limits = usage?.limits;

  return (
    <div className="login-panel">
      <h2 className="panel-title">AI Model</h2>
      <form className="login-form" onSubmit={handleSave}>
        <div className="form-row">
          <label>Provider</label>
          <select value={provider} onChange={(e) => setProvider(e.target.value as "anthropic" | "groq" | "ollama")}>
            <option value="anthropic">Claude ({settings.claude_model})</option>
            <option value="groq">Groq (free tier)</option>
            <option value="ollama">Ollama (runs on this computer)</option>
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

        {provider === "ollama" && (
          <>
            <div className="form-row">
              <label>Local model</label>
              <select value={ollamaModel} onChange={(e) => setOllamaModel(e.target.value)} disabled={!ollama.reachable}>
                <option value="">
                  Auto — best installed{ollama.auto_model ? ` (${ollama.auto_model})` : ""}
                </option>
                {ollama.models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.id} · {m.size_gb} GB{m.tools ? "" : " · no tools"}
                    {m.too_small ? " · very small" : ""}
                    {m.fits ? "" : " · too big for this PC"}
                  </option>
                ))}
              </select>
            </div>
            <p className="model-note">
              Runs entirely on this computer ({ollama.url}): nothing is sent over the network, there are no rate limits
              and no API key. Auto picks the best installed model that fits in memory
              {ollama.ram_gb ? ` (${ollama.ram_gb} GB here)` : ""}. Local models are slower than Claude and Groq, and
              the AI steps run one at a time.
              {ollama.hidden_cloud > 0 &&
                ` ${ollama.hidden_cloud} Ollama cloud model${ollama.hidden_cloud !== 1 ? "s are" : " is"} hidden because they don't run locally.`}
            </p>
            {ollama.notice && <p className="form-error">{ollama.notice}</p>}
            {!ollama.reachable && ollama.suggested.length > 0 && (
              <p className="model-note">After starting Ollama, click Refresh.</p>
            )}
            <div className="login-actions">
              <button type="button" className="btn btn-secondary" onClick={handleRefreshOllama} disabled={refreshing}>
                {refreshing ? "Refreshing…" : "Refresh models"}
              </button>
            </div>
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

        {usage && usage.provider === "ollama" && (
          <div className="usage-meter">
            <div className="model-note">Today ({usage.model || "no model"})</div>
            <div className="model-note">
              {usage.requests.toLocaleString()} requests · {usage.tokens.toLocaleString()} tokens (no limits)
            </div>
          </div>
        )}

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
