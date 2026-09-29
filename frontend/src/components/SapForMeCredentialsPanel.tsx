import { useEffect, useState } from "react";
import { api } from "../api/client";

export default function SapForMeCredentialsPanel() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [saving, setSaving] = useState(false);
  const [savedNotice, setSavedNotice] = useState(false);

  useEffect(() => {
    api.loadSapForMeCredentials().then((creds) => {
      if (creds.email) setEmail(creds.email);
      if (creds.password) setPassword(creds.password);
    }).catch(() => {});
  }, []);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setSavedNotice(false);
    try {
      await api.saveSapForMeCredentials({ email, password });
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
