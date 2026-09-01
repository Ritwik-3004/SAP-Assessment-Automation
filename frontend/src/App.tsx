import { useState } from "react";
import LoginPanel from "./components/LoginPanel";
import TaanaPanel from "./components/transactions/TaanaPanel";
import Db15Panel from "./components/transactions/Db15Panel";
import Se16nPanel from "./components/transactions/Se16nPanel";
import Se11Panel from "./components/transactions/Se11Panel";
import AobjPanel from "./components/transactions/AobjPanel";
import SaraPanel from "./components/transactions/SaraPanel";
import type { ConnectionState, TransactionId } from "./types";
import "./App.css";

const TRANSACTIONS: { id: TransactionId; label: string }[] = [
  { id: "TAANA", label: "TAANA — Table Analysis" },
  { id: "DB15",  label: "DB15 — Find Archiving Objects" },
  { id: "SE16N", label: "SE16N — Table Browser" },
  { id: "SE11",  label: "SE11 — Dictionary" },
  { id: "AOBJ",  label: "AOBJ — Archiving Objects" },
  { id: "SARA",  label: "SARA — Archive Sessions" },
];

export default function App() {
  const [connection, setConnection] = useState<ConnectionState>({ connected: false });
  const [activeTab, setActiveTab] = useState<TransactionId>("TAANA");

  function handleConnected(state: ConnectionState) {
    setConnection(state);
  }

  function handleDisconnected() {
    setConnection({ connected: false });
  }

  const panelMap: Record<TransactionId, React.ReactNode> = {
    TAANA: <TaanaPanel />,
    DB15:  <Db15Panel />,
    SE16N: <Se16nPanel />,
    SE11:  <Se11Panel />,
    AOBJ:  <AobjPanel />,
    SARA:  <SaraPanel />,
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
              <p className="nav-label">Transactions</p>
              {TRANSACTIONS.map((tx) => (
                <button
                  key={tx.id}
                  className={`nav-item ${activeTab === tx.id ? "active" : ""}`}
                  onClick={() => setActiveTab(tx.id)}
                >
                  {tx.label}
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
                Connect to your SAP system using the panel on the left to begin
                running transactions.
              </p>
              <ul className="tx-list">
                {TRANSACTIONS.map((tx) => (
                  <li key={tx.id}>{tx.label}</li>
                ))}
              </ul>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
