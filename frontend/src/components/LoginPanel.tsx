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
  const [system, setSystem] = useState("");
  const [client, setClient] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [language, setLanguage] = useState("EN");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api.systems().then((r) => setSystems(r.systems)).catch(() => {});
  }, []);

  async function handleConnect(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");
    try {
      const res = await api.connect({ system, client, username, password, language });
      onConnected({ connected: true, system: res.system, user: res.user });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Connection failed");
    } finally {
      setLoading(false);
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

        <button type="submit" className="btn btn-primary" disabled={loading}>
          {loading ? "Connecting…" : "Connect"}
        </button>
      </form>
    </div>
  );
}
