import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { ConnectionState, SapSystem, SavedSapCredentials } from "../types";

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
  const [profiles, setProfiles] = useState<SavedSapCredentials[]>([]);
  const [selectedProfile, setSelectedProfile] = useState(-1);

  const profileLabel = (p: SavedSapCredentials) => `${p.username} @ ${p.system} (client ${p.client})`;

  function fillFrom(p: Partial<SavedSapCredentials>) {
    if (p.system) setSystem(p.system);
    if (p.client) setClient(p.client);
    if (p.username) setUsername(p.username);
    if (p.password !== undefined) setPassword(p.password);
    if (p.language) setLanguage(p.language);
  }

  function sameProfile(p: SavedSapCredentials, q: Partial<SavedSapCredentials>) {
    const n = (v?: string) => (v ?? "").trim().toLowerCase();
    return n(p.system) === n(q.system) && n(p.client) === n(q.client) && n(p.username) === n(q.username);
  }

  useEffect(() => {
    api.systems().then((r) => setSystems(r.systems)).catch(() => {});
    // Load saved credentials from the server file (persists across browser sessions)
    api.loadCredentials().then((creds) => {
      const list = creds.profiles ?? [];
      setProfiles(list);
      fillFrom(creds);
      setSelectedProfile(list.findIndex((p) => sameProfile(p, creds)));
    }).catch(() => {});
  }, []);

  function handleProfileChange(value: string) {
    const index = Number(value);
    setSelectedProfile(index);
    if (index < 0) return;
    const p = profiles[index];
    fillFrom(p);
    api.selectCredentials(p).catch(() => {});
  }

  async function handleDeleteProfile() {
    if (selectedProfile < 0) return;
    const p = profiles[selectedProfile];
    await api.deleteCredentials(p).catch(() => {});
    const creds = await api.loadCredentials().catch(() => ({ profiles: [] as SavedSapCredentials[] }));
    setProfiles(creds.profiles ?? []);
    setSelectedProfile(-1);
  }

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
      const creds = await api.loadCredentials();
      const list = creds.profiles ?? [];
      setProfiles(list);
      setSelectedProfile(list.findIndex((p) => sameProfile(p, { system, client, username })));
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
        {profiles.length > 0 && (
          <div className="form-row">
            <label>Saved connections</label>
            <div className="saved-profile-row">
              <select value={selectedProfile} onChange={(e) => handleProfileChange(e.target.value)}>
                <option value={-1}>— choose a saved connection —</option>
                {profiles.map((p, i) => (
                  <option key={`${p.system}|${p.client}|${p.username}`} value={i}>{profileLabel(p)}</option>
                ))}
              </select>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={handleDeleteProfile}
                disabled={selectedProfile < 0}
                title="Remove the selected saved connection"
              >
                Delete
              </button>
            </div>
          </div>
        )}

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
