import { useState, useEffect } from "react";
import LoginPanel from "./components/LoginPanel";
import SapForMeCredentialsPanel from "./components/SapForMeCredentialsPanel";
import ModelSettingsPanel from "./components/ModelSettingsPanel";
import BatchArchivingPanel from "./components/BatchArchivingPanel";
import GenerateTableListPanel from "./components/GenerateTableListPanel";
import HeaderTablePanel from "./components/HeaderTablePanel";
import TableAnalysisPanel from "./components/TableAnalysisPanel";
import ObjectAnalysisPanel from "./components/ObjectAnalysisPanel";
import OpenItemAnalysisPanel from "./components/OpenItemAnalysisPanel";
import FinalReportPanel from "./components/FinalReportPanel";
import AssistantPanel, { type AssistantMode } from "./components/AssistantPanel";
import { ChevronLeft, ChevronRight, TaskIcon } from "./components/icons";
import { AssistantProvider } from "./assistantContext";
import ObjectAnalysisBadge from "./components/ObjectAnalysisBadge";
import OpenItemAnalysisBadge from "./components/OpenItemAnalysisBadge";
import RunningJobsBadge from "./components/RunningJobsBadge";
import { api } from "./api/client";
import type { ConnectionState } from "./types";
import "./App.css";

const TASKS = [
  { id: "TOP_TABLES", label: "Generate Table List" },
  { id: "BATCH", label: "Find Archiving Objects for Tables" },
  { id: "HEADER_TABLES", label: "Find Header Tables for Archiving Objects" },
  { id: "TABLE_ANALYSIS", label: "Table Analysis" },
  { id: "OBJECT_ANALYSIS", label: "Archiving Object Analysis" },
  { id: "OPEN_ITEM_ANALYSIS", label: "Open Item Analysis" },
  { id: "FINAL_REPORT", label: "Final report generation" },
] as const;

type Tab = (typeof TASKS)[number]["id"];

// The side panels remember how the user left them. A narrow window starts with both collapsed.
const NARROW_PX = 1200;
const SIDEBAR_KEY = "sap.sidebarCollapsed";
const ASSISTANT_KEY = "sap.assistantDocked";

function loadFlag(key: string, fallback: boolean): boolean {
  try {
    const v = localStorage.getItem(key);
    return v === null ? fallback : v === "1";
  } catch {
    return fallback;
  }
}

function saveFlag(key: string, value: boolean) {
  try {
    localStorage.setItem(key, value ? "1" : "0");
  } catch {
    // Not remembered; the panels still work
  }
}

export default function App() {
  const [connection, setConnection] = useState<ConnectionState>({ connected: false });
  const [activeTab, setActiveTab] = useState<Tab>("TOP_TABLES");
  const [modelLabel, setModelLabel] = useState("");
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => loadFlag(SIDEBAR_KEY, window.innerWidth < NARROW_PX));
  // docked: beside the page; collapsed: a floating button; popup: floating over the page
  const [assistantMode, setAssistantMode] = useState<AssistantMode>(() =>
    loadFlag(ASSISTANT_KEY, window.innerWidth >= NARROW_PX) ? "docked" : "collapsed"
  );

  function toggleSidebar() {
    setSidebarCollapsed((collapsed) => {
      saveFlag(SIDEBAR_KEY, !collapsed);
      return !collapsed;
    });
  }

  function changeAssistantMode(mode: AssistantMode) {
    setAssistantMode(mode);
    saveFlag(ASSISTANT_KEY, mode === "docked");
  }

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
    OPEN_ITEM_ANALYSIS: <OpenItemAnalysisPanel />,
    FINAL_REPORT: <FinalReportPanel />,
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
          <OpenItemAnalysisBadge />
          <RunningJobsBadge />
          {connection.connected ? (
            <span className="status-badge connected">Connected — {connection.system}</span>
          ) : (
            <span className="status-badge disconnected">Not Connected</span>
          )}
        </div>
      </header>

      <main className="app-main">
        <aside className={`sidebar${sidebarCollapsed ? " collapsed" : ""}`}>
          <div className="sidebar-clip">
            <div className="sidebar-full" inert={sidebarCollapsed}>
              <div className="sidebar-top">
                <span>Workspace</span>
                <button
                  type="button"
                  className="icon-btn"
                  onClick={toggleSidebar}
                  aria-label="Collapse sidebar"
                  aria-expanded={!sidebarCollapsed}
                >
                  <ChevronLeft />
                </button>
              </div>

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
                      <span className="nav-icon">
                        <TaskIcon id={task.id} />
                      </span>
                      {task.label}
                    </button>
                  ))}
                </nav>
              )}
            </div>
          </div>

          <div className="sidebar-rail" inert={!sidebarCollapsed}>
            <button
              type="button"
              className="icon-btn"
              onClick={toggleSidebar}
              aria-label="Expand sidebar"
              aria-expanded={!sidebarCollapsed}
            >
              <ChevronRight />
            </button>
            <span
              className={`rail-dot${connection.connected ? " on" : ""}`}
              data-tip={connection.connected ? `Connected — ${connection.system}` : "Not connected"}
            />
            {connection.connected &&
              TASKS.map((task) => (
                <button
                  key={task.id}
                  type="button"
                  className={`rail-btn${activeTab === task.id ? " active" : ""}`}
                  data-tip={task.label}
                  aria-label={task.label}
                  onClick={() => setActiveTab(task.id)}
                >
                  <TaskIcon id={task.id} />
                </button>
              ))}
          </div>
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

        <AssistantPanel mode={assistantMode} onModeChange={changeAssistantMode} />
      </main>
    </div>
    </AssistantProvider>
  );
}
