import { useState, useEffect } from "react";
import LoginPanel from "./components/LoginPanel";
import SapForMeCredentialsPanel from "./components/SapForMeCredentialsPanel";
import ModelSettingsPanel from "./components/ModelSettingsPanel";
import BatchArchivingPanel from "./components/BatchArchivingPanel";
import GenerateTableListPanel from "./components/GenerateTableListPanel";
import HeaderTablePanel from "./components/HeaderTablePanel";
import TableAnalysisPanel from "./components/TableAnalysisPanel";
import ObjectAnalysisPanel from "./components/ObjectAnalysisPanel";
import AssistantPanel from "./components/AssistantPanel";
import { AssistantProvider } from "./assistantContext";
import ObjectAnalysisBadge from "./components/ObjectAnalysisBadge";
import { api } from "./api/client";
import type { ConnectionState } from "./types";
import "./App.css";

const TASKS = [
  { id: "TOP_TABLES", label: "Generate Table List (DB02)" },
  { id: "BATCH", label: "Find Archiving Objects for Tables" },
  { id: "HEADER_TABLES", label: "Find Header Tables for Archiving Objects" },
  { id: "TABLE_ANALYSIS", label: "Table Analysis (TAANA)" },
  { id: "OBJECT_ANALYSIS", label: "Archiving Object Analysis" },
] as const;

type Tab = (typeof TASKS)[number]["id"];

export default function App() {
  const [connection, setConnection] = useState<ConnectionState>({ connected: false });
  const [activeTab, setActiveTab] = useState<Tab>("TOP_TABLES");
  const [modelLabel, setModelLabel] = useState("");

  // On mount, ask the backend whether a SAP session is already open (e.g. after
  // a page refresh) and restore the connected state so the user doesn't have to
  // re-enter credentials.
  useEffect(() => {
    api.sapInfo().then((info) => {
      if (info.connected && info.system) {
        setConnection({ connected: true, system: info.system, user: info.user ?? "" });
      }
    }).catch(() => {});
  }, []);

  function handleConnected(state: ConnectionState) {
    setConnection(state);
  }

  function handleDisconnected() {
    setConnection({ connected: false });
  }

  const panelMap: Record<Tab, React.ReactNode> = {
    TOP_TABLES: <GenerateTableListPanel />,
    BATCH: <BatchArchivingPanel />,
    HEADER_TABLES: <HeaderTablePanel />,
    TABLE_ANALYSIS: <TableAnalysisPanel />,
    OBJECT_ANALYSIS: <ObjectAnalysisPanel />,
  };

  return (
    <AssistantProvider>
    <div className="app">
      <header className="app-header">
        <div className="header-brand">
          <span className="header-logo">⬡</span>
          <span className="header-title">SAP Assessment Automation</span>
        </div>
        <div className="header-status">
          {modelLabel && (
            <span className="status-badge model-badge" title="AI model used for every AI step">
              AI: {modelLabel}
            </span>
          )}
          <ObjectAnalysisBadge />
          {connection.connected ? (
            <span className="status-badge connected">Connected — {connection.system}</span>
          ) : (
            <span className="status-badge disconnected">Not Connected</span>
          )}
        </div>
      </header>

      <main className="app-main">
        <aside className="sidebar">
          <LoginPanel
            connection={connection}
            onConnected={handleConnected}
            onDisconnected={handleDisconnected}
          />

          <ModelSettingsPanel onChanged={setModelLabel} />

          <SapForMeCredentialsPanel />

          {connection.connected && (
            <nav className="tx-nav">
              <p className="nav-label">Tasks</p>
              {TASKS.map((task) => (
                <button
                  key={task.id}
                  className={`nav-item ${activeTab === task.id ? "active" : ""}`}
                  onClick={() => setActiveTab(task.id)}
                >
                  {task.label}
                </button>
              ))}
            </nav>
          )}
        </aside>

        <section className="content">
          {connection.connected ? (
            panelMap[activeTab]
          ) : (
            <div className="welcome">
              <h1>SAP Archivability Assessment</h1>
              <p>
                Connect to your SAP system using the panel on the left to get started.
              </p>
              <ul className="tx-list">
                {TASKS.map((task) => (
                  <li key={task.id}>{task.label}</li>
                ))}
              </ul>
            </div>
          )}
        </section>

        <AssistantPanel />
      </main>
    </div>
    </AssistantProvider>
  );
}
