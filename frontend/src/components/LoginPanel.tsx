import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ConnectionState, SapSystem } from "../types";

interface Props {
  onConnected: (state: ConnectionState) => void;
  onDisconnected: () => void;
  connection: ConnectionState;
}

export default function LoginPanel({ onConnected, onDisconnected, connection }: Props) {
  const [systems, setSystems] = useState<SapSystem[]>([]);
  const [system, setSystem] = useState(() => localStorage.getItem("sap_system") ?? "");
  const [client, setClient] = useState(() => localStorage.getItem("sap_client") ?? "");
  const [username, setUsername] = useState(() => localStorage.getItem("sap_username") ?? "");
  const [password, setPassword] = useState("");
  const [language, setLanguage] = useState(() => localStorage.getItem("sap_language") ?? "EN");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedNotice, setSavedNotice] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.systems().then((r) => setSystems(r.systems)).catch(() => {});
    // Load saved credentials from the server file (persists across browser sessions)
    api.loadCredentials().then((creds) => {
      if (creds.system) setSystem(creds.system);
      if (creds.client) setClient(creds.client);
      if (creds.username) setUsername(creds.username);
      if (creds.password) setPassword(creds.password);
      if (creds.language) setLanguage(creds.language);
    }).catch(() => {});
  }, []);

  async function handleConnect(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      const res = await api.connect({ system, client, username, password, language });
      localStorage.setItem("sap_system", system);
      localStorage.setItem("sap_client", client);
      localStorage.setItem("sap_username", username);
      localStorage.setItem("sap_language", language);
      onConnected({ connected: true, system: res.system, user: res.user });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Connection failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleSave() {
    setSaving(true);
    setSavedNotice(false);
    try {
      await api.saveCredentials({ system, client, username, password, language });
      setSavedNotice(true);
      setTimeout(() => setSavedNotice(false), 3000);
    } finally {
      setSaving(false);
    }
  }

  async function handleDisconnect() {
    setLoading(true);
    try {
      await api.disconnect();
      onDisconnected();
    } finally {
      setLoading(false);
    }
  }

  if (connection.connected) {
    return (
      <div className="login-panel connected">
        <div className="conn-info">
          <span className="conn-dot" />
          <span>
            Connected to <strong>{connection.system}</strong> as <strong>{connection.user}</strong>
          </span>
        </div>
        <button className="btn btn-danger" onClick={handleDisconnect} disabled={loading}>
          {loading ? "Disconnecting…" : "Disconnect"}
        </button>
      </div>
    );
  }

  return (
    <div className="login-panel">
      <h2 className="panel-title">Connect to SAP</h2>
      <form className="login-form" onSubmit={handleConnect}>
        <div className="form-row">
          <label>SAP System</label>
          {systems.length > 0 ? (
            <select value={system} onChange={(e) => setSystem(e.target.value)} required>
              <option value="">— select —</option>
              {systems.map((s) => (
                <option key={s.id} value={s.description}>{s.description}</option>
              ))}
            </select>
          ) : (
            <input
              type="text"
              placeholder="e.g. PRD (system description from SAP Logon)"
              value={system}
              onChange={(e) => setSystem(e.target.value)}
              required
            />
          )}
        </div>

        <div className="form-row">
          <label>Client</label>
          <input
            type="text"
            placeholder="e.g. 100"
            value={client}
            onChange={(e) => setClient(e.target.value)}
            required
            maxLength={3}
          />
        </div>

        <div className="form-row">
          <label>Username</label>
          <input
            type="text"
            placeholder="SAP username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
            autoComplete="username"
          />
        </div>

        <div className="form-row">
          <label>Password</label>
          <input
            type="password"
            placeholder="SAP password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoComplete="current-password"
          />
        </div>

        <div className="form-row">
          <label>Language</label>
          <select value={language} onChange={(e) => setLanguage(e.target.value)}>
            <option value="EN">English (EN)</option>
            <option value="DE">German (DE)</option>
          </select>
        </div>

        {error && <p className="form-error">{error}</p>}

        <div className="login-actions">
          <button type="submit" className="btn btn-primary" disabled={loading}>
            {loading ? "Connecting…" : "Connect"}
          </button>
          <button type="button" className="btn btn-secondary" onClick={handleSave} disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
        {savedNotice && <p className="saved-notice">Credentials saved.</p>}
      </form>
    </div>
  );
}
