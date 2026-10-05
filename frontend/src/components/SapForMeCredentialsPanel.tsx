import { useEffect, useState } from "react";
import { api } from "../api/client";

export default function SapForMeCredentialsPanel() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [saving, setSaving] = useState(false);
  const [savedNotice, setSavedNotice] = useState(false);
  const [profiles, setProfiles] = useState<{ email: string; password: string }[]>([]);
  const [selectedEmail, setSelectedEmail] = useState("");

  const sameEmail = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();

  useEffect(() => {
    api.loadSapForMeCredentials().then((creds) => {
      setProfiles(creds.profiles ?? []);
      if (creds.email) setEmail(creds.email);
      if (creds.password) setPassword(creds.password);
      setSelectedEmail(creds.email ?? "");
    }).catch(() => {});
  }, []);

  function handleProfileChange(value: string) {
    setSelectedEmail(value);
    const p = profiles.find((x) => x.email === value);
    if (!p) return;
    setEmail(p.email);
    setPassword(p.password);
    api.selectSapForMeCredentials(p.email).catch(() => {});
  }

  async function handleDeleteProfile() {
    if (!selectedEmail) return;
    await api.deleteSapForMeCredentials(selectedEmail).catch(() => {});
    const creds = await api.loadSapForMeCredentials().catch(() => ({ profiles: [] }));
    setProfiles(creds.profiles ?? []);
    setSelectedEmail("");
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setSavedNotice(false);
    try {
      await api.saveSapForMeCredentials({ email, password });
      const creds = await api.loadSapForMeCredentials();
      setProfiles(creds.profiles ?? []);
      setSelectedEmail(creds.profiles?.find((p) => sameEmail(p.email, email))?.email ?? "");
      setSavedNotice(true);
      setTimeout(() => setSavedNotice(false), 3000);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="login-panel">
      <h2 className="panel-title">SAP for Me Credentials</h2>
      <form className="login-form" onSubmit={handleSave}>
        {profiles.length > 0 && (
          <div className="form-row">
            <label>Saved accounts</label>
            <div className="saved-profile-row">
              <select value={selectedEmail} onChange={(e) => handleProfileChange(e.target.value)}>
                <option value="">— choose a saved account —</option>
                {profiles.map((p) => (
                  <option key={p.email} value={p.email}>{p.email}</option>
                ))}
              </select>
              <button
                type="button"
                className="btn btn-secondary"
                onClick={handleDeleteProfile}
                disabled={!selectedEmail}
                title="Remove the selected saved account"
              >
                Delete
              </button>
            </div>
          </div>
        )}

        <div className="form-row">
          <label>Email</label>
          <input
            type="email"
            placeholder="SAP ID email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            required
            autoComplete="username"
          />
        </div>

        <div className="form-row">
          <label>Password</label>
          <input
            type="password"
            placeholder="SAP ID password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
            autoComplete="current-password"
          />
        </div>

        <div className="login-actions">
          <button type="submit" className="btn btn-secondary" disabled={saving}>
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
        {savedNotice && <p className="saved-notice">Credentials saved.</p>}
      </form>
    </div>
  );
}
