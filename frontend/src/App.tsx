import { useState } from "react";
import LoginPanel from "./components/LoginPanel";
import BatchArchivingPanel from "./components/BatchArchivingPanel";
import GenerateTableListPanel from "./components/GenerateTableListPanel";
import type { ConnectionState } from "./types";
import "./App.css";

const TASKS = [
  { id: "TOP_TABLES", label: "Generate Table List (DB02)" },
  { id: "BATCH", label: "Find Archiving Objects for Tables" },
] as const;

type Tab = (typeof TASKS)[number]["id"];

export default function App() {
  const [connection, setConnection] = useState<ConnectionState>({ connected: false });
  const [activeTab, setActiveTab] = useState<Tab>("TOP_TABLES");

  function handleConnected(state: ConnectionState) {
    setConnection(state);
  }

  function handleDisconnected() {
    setConnection({ connected: false });
  }

  const panelMap: Record<Tab, React.ReactNode> = {
    TOP_TABLES: <GenerateTableListPanel />,
    BATCH: <BatchArchivingPanel />,
  };

  return (
    <div className="app">
      <header className="app-header">
        <div className="header-brand">
          <span className="header-logo">⬡</span>
          <span className="header-title">SAP Assessment Automation</span>
        </div>
        <div className="header-status">
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
      </main>
    </div>
  );
}
