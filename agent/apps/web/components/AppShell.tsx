"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useGlobalKeyboardShortcuts } from "@/hooks/useKeyboardShortcuts";
import { SessionSidebar } from "./SessionSidebar";
import { ChatWindow } from "./ChatWindow";
import { SettingsModal } from "./SettingsModal";
import { HelpModal } from "./HelpModal";
import { ProjectTrustDialog } from "./ProjectTrustDialog";
import { useTheme } from "@/hooks/useTheme";
import { useI18n } from "@/hooks/useI18n";
import { useIsMobile } from "@/hooks/useIsMobile";
import { useViewportHeight } from "@/hooks/useViewportHeight";
import { useResizablePanel } from "@/hooks/useResizablePanel";
import { getInitialNavigation } from "@/lib/initial-navigation";
import {
  getSidebarMaxWidth,
  SIDEBAR_DEFAULT_WIDTH,
  SIDEBAR_MAX_WIDTH,
  SIDEBAR_MIN_WIDTH,
} from "@/lib/panel-layout";
import type { SessionInfo } from "@/lib/types";
import type { ProjectTrustStatus } from "@/lib/api-types";
import type { ChatInputHandle } from "./ChatInput";
import { CircleHelp, FolderPlus, Moon, PanelLeftOpen, PlusCircle, Settings, ShieldAlert, Sun } from "lucide-react";

export function AppShell() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [initialNavigation] = useState(() => getInitialNavigation(searchParams));
  const { isDark, toggleTheme } = useTheme();
  const { t: translate } = useI18n();
  const isMobile = useIsMobile();
  useViewportHeight();
  const [selectedSession, setSelectedSession] = useState<SessionInfo | null>(null);
  // When user clicks +, we only store the cwd — no fake session id
  const [newSessionCwd, setNewSessionCwd] = useState<string | null>(null);
  const [initialCwdStatus, setInitialCwdStatus] = useState<"idle" | "validating" | "ready" | "error">(
    () => initialNavigation.requestedCwd ? "validating" : "idle",
  );
  const [initialCwdError, setInitialCwdError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [sessionKey, setSessionKey] = useState(0);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [modelsRefreshKey, setModelsRefreshKey] = useState(0);
  const [projectTrust, setProjectTrust] = useState<ProjectTrustStatus | null>(null);
  const [projectTrustDialogOpen, setProjectTrustDialogOpen] = useState(false);
  const [projectTrustBusy, setProjectTrustBusy] = useState(false);
  const [projectTrustError, setProjectTrustError] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [mobileSidebarReady, setMobileSidebarReady] = useState(false);
  const sidebarWidthRef = useRef(SIDEBAR_DEFAULT_WIDTH);
  const getResponsiveSidebarMaxWidth = useCallback(
    () => typeof window === "undefined"
      ? SIDEBAR_MAX_WIDTH
      : getSidebarMaxWidth(window.innerWidth),
    [],
  );
  const sidebarResizer = useResizablePanel({
    ariaLabel: translate("layout.resizeSidebar"),
    cssVariable: "--sidebar-width",
    defaultWidth: SIDEBAR_DEFAULT_WIDTH,
    getMaxWidth: getResponsiveSidebarMaxWidth,
    growthDirection: "right",
    maxWidth: SIDEBAR_MAX_WIDTH,
    minWidth: SIDEBAR_MIN_WIDTH,
    storageKey: "pi-sidebar-width",
    widthRef: sidebarWidthRef,
  });
  const reclampSidebarWidth = sidebarResizer.reclampWidth;
  // On mobile the sidebar is an overlay drawer; hide it by default so the chat
  // is visible on load. Runs once the breakpoint resolves after hydration.
  useEffect(() => {
    if (isMobile) setSidebarOpen(false);
  }, [isMobile]);
  useEffect(() => {
    setMobileSidebarReady(true);
  }, []);
  useEffect(() => { reclampSidebarWidth(); }, [reclampSidebarWidth]);
  const chatInputRef = useRef<ChatInputHandle | null>(null);

  const handleAgentConfigureMcp = useCallback((serverName?: string) => {
    const target = serverName ? `“${serverName}” MCP Server` : "当前项目需要的 MCP Server";
    const prompt = `请为当前项目安装并配置${target}。\n\n安装、依赖和环境检查由你完成；配置写入当前项目的 .pi/mcp.json。不要修改 Cursor、Claude、Codex 或其他客户端的 MCP 配置。完成后说明你做了什么以及如何验证。`;
    setSettingsOpen(false);
    window.setTimeout(() => chatInputRef.current?.insertIfEmpty(prompt), 0);
  }, []);

  const handleSidebarToggle = useCallback(() => {
    setSidebarOpen((open) => !open);
  }, []);

  const initialSessionId = initialNavigation.sessionId;
  const [activeCwd, setActiveCwd] = useState<string | null>(null);
  const activeProjectRootRef = useRef<string | null>(null);
  // True once the initial ?session= URL param has been resolved (or confirmed absent)
  const [initialSessionRestored, setInitialSessionRestored] = useState<boolean>(() => !initialSessionId);
  // Suppresses sessionKey bump in handleCwdChange during the initial URL restore
  const suppressCwdBumpRef = useRef(false);

  useEffect(() => {
    const requestedCwd = initialNavigation.requestedCwd;
    if (!requestedCwd) return;

    const controller = new AbortController();
    setInitialCwdStatus("validating");
    setInitialCwdError(null);

    void fetch("/api/cwd/validate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ cwd: requestedCwd }),
      signal: controller.signal,
    })
      .then(async (response) => {
        const data = await response.json().catch(() => ({})) as { cwd?: string; error?: string };
        if (!response.ok || !data.cwd) {
          throw new Error(data.error ?? `HTTP ${response.status}`);
        }

        // The sidebar will notify us when it adopts this cwd. Avoid remounting
        // the just-created empty chat during that initial synchronization.
        suppressCwdBumpRef.current = true;
        setNewSessionCwd(data.cwd);
        setInitialCwdStatus("ready");
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        setInitialCwdError(error instanceof Error ? error.message : String(error));
        setInitialCwdStatus("error");
      });

    return () => controller.abort();
  }, [initialNavigation]);

  const handleCwdChange = useCallback((cwd: string | null, projectRoot?: string | null) => {
    setActiveCwd(cwd);
    // Skip if cwd is null (initial mount).
    if (!cwd) return;
    if (initialCwdStatus === "error") {
      setInitialCwdStatus("ready");
      setInitialCwdError(null);
    }
    const newProject = projectRoot ?? cwd;
    const currentProject = activeProjectRootRef.current
      ?? (selectedSession ? (selectedSession.projectRoot ?? selectedSession.cwd) : null);
    activeProjectRootRef.current = newProject;

    // Keep the project identity in sync during the initial URL restore without
    // remounting the just-created or restored chat.
    if (suppressCwdBumpRef.current) {
      suppressCwdBumpRef.current = false;
      return;
    }
    // A draft chat is still editable: changing the workspace from the
    // picker should retarget that draft instead of closing it.
    if (!selectedSession && newSessionCwd) {
      setNewSessionCwd(cwd);
      activeProjectRootRef.current = newProject;
      return;
    }
    // Worktrees of one repo share a project root. Moving the effective cwd
    // within the same project (e.g. switching worktree, or clicking a session
    // that lives in another worktree) must not close the open session.
    if (currentProject === newProject) {
      return;
    }
    // Close any session that belongs to a different project — it no longer
    // matches the selected project directory.
    setSelectedSession(null);
    setNewSessionCwd((prev) => {
      if (prev && prev !== cwd) return null;
      return prev;
    });
    setSessionKey((k) => k + 1);
    router.replace("/", { scroll: false });
  }, [initialCwdStatus, router, selectedSession, newSessionCwd]);

  const handleSelectSession = useCallback((session: SessionInfo, isRestore = false) => {
    setNewSessionCwd(null);
    setSelectedSession(session);
    setSessionKey((k) => k + 1);
    setInitialSessionRestored(true);
    // On mobile, collapse the overlay drawer so the chat is revealed after pick.
    if (isMobile && !isRestore) setSidebarOpen(false);
    if (isRestore) {
      // Suppress the redundant sessionKey bump that would come from the
      // onCwdChange effect firing after setSelectedCwd in the sidebar
      suppressCwdBumpRef.current = true;
    }
    // Skip router.replace when restoring from URL — the param is already correct
    // and calling replace in production Next.js triggers a Suspense remount loop
    if (!isRestore) {
      router.replace(`?session=${encodeURIComponent(session.id)}`, { scroll: false });
    }
  }, [router, isMobile]);

  const handleNewSession = useCallback((_sessionId: string, cwd: string) => {
    setSelectedSession(null);
    setNewSessionCwd(cwd);
    setSessionKey((k) => k + 1);
    if (isMobile) setSidebarOpen(false);
    router.replace("/", { scroll: false });
  }, [router, isMobile]);

  // Global keyboard shortcuts (handles Esc, Ctrl+Alt+N etc.)
  useGlobalKeyboardShortcuts({
    onNewSession: (cwd: string) => handleNewSession(`kb-${Date.now()}`, cwd),
    activeCwd,
  });

  // Client-built transient SessionInfo (new session / fork) lacks the
  // server-computed projectRoot, which the same-project check in
  // handleCwdChange relies on. Hydrate it from the session list so switching
  // worktrees right after creating a session doesn't close the chat.
  const hydrateSelectedSession = useCallback((sessionId: string) => {
    void fetch("/api/sessions")
      .then((r) => (r.ok ? (r.json() as Promise<{ sessions: SessionInfo[] }>) : null))
      .then((d) => {
        const full = d?.sessions.find((s) => s.id === sessionId);
        if (!full) return;
        setSelectedSession((prev) => (prev && prev.id === sessionId && !prev.projectRoot ? full : prev));
      })
      .catch(() => {});
  }, []);

  // Called by ChatWindow when a new session gets its real id from pi
  const handleSessionCreated = useCallback((session: SessionInfo) => {
    setNewSessionCwd(null);
    setSelectedSession(session);
    setRefreshKey((k) => k + 1);
    hydrateSelectedSession(session.id);
    router.replace(`?session=${encodeURIComponent(session.id)}`, { scroll: false });
  }, [router, hydrateSelectedSession]);

  const handleAgentEnd = useCallback(() => {
    setRefreshKey((k) => k + 1);
  }, []);

  const handleSessionForked = useCallback((newSessionId: string) => {
    setRefreshKey((k) => k + 1);
    setSessionKey((k) => k + 1);
    setNewSessionCwd(null);
    setSelectedSession((prev) => ({
      ...(prev ?? { path: "", cwd: "", created: "", modified: "", messageCount: 0, firstMessage: "" }),
      id: newSessionId,
    }));
    hydrateSelectedSession(newSessionId);
    router.replace(`?session=${encodeURIComponent(newSessionId)}`, { scroll: false });
  }, [router, hydrateSelectedSession]);

  const handleInitialRestoreDone = useCallback(() => {
    setInitialSessionRestored(true);
  }, []);

  const handleSessionDeleted = useCallback((sessionId: string, affectedSessionIds: string[] = [sessionId]) => {
    setRefreshKey((k) => k + 1);
    if (selectedSession && affectedSessionIds.includes(selectedSession.id)) {
      const cwd = selectedSession.cwd;
      setSelectedSession(null);
      setNewSessionCwd(cwd ?? null);
      setSessionKey((k) => k + 1);
      router.replace("/", { scroll: false });
    }
  }, [selectedSession, router]);

  // Show a restored session or an empty draft. Once initial navigation settles,
  // the effect below opens a draft in the active project by default.
  const effectiveNewSessionCwd = newSessionCwd;
  const showChat = selectedSession !== null || effectiveNewSessionCwd !== null;
  const projectTrustCwd = selectedSession?.cwd ?? effectiveNewSessionCwd;
  // While restoring initial session from URL, don't show the placeholder
  const showPlaceholder = initialSessionRestored && !showChat;

  useEffect(() => {
    if (!initialSessionRestored) return;
    if (initialCwdStatus === "validating" || initialCwdStatus === "error") return;
    if (selectedSession || newSessionCwd || !activeCwd) return;
    setNewSessionCwd(activeCwd);
    setSessionKey((key) => key + 1);
  }, [activeCwd, initialCwdStatus, initialSessionRestored, newSessionCwd, selectedSession]);

  useEffect(() => {
    setProjectTrust(null);
    setProjectTrustDialogOpen(false);
    setProjectTrustError(null);
    if (!projectTrustCwd) return;

    const controller = new AbortController();
    fetch(`/api/project-trust?cwd=${encodeURIComponent(projectTrustCwd)}`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        const data = await response.json() as ProjectTrustStatus & { error?: string };
        if (!response.ok || data.error) throw new Error(data.error ?? `HTTP ${response.status}`);
        setProjectTrust(data);
      })
      .catch((error) => {
        if (error instanceof DOMException && error.name === "AbortError") return;
        console.error("Failed to load project trust:", error);
      });
    return () => controller.abort();
  }, [projectTrustCwd]);

  const handleTrustProject = useCallback(async () => {
    if (!projectTrustCwd || projectTrustBusy) return;
    setProjectTrustBusy(true);
    setProjectTrustError(null);
    try {
      const response = await fetch("/api/project-trust", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cwd: projectTrustCwd }),
      });
      const data = await response.json() as ProjectTrustStatus & { error?: string };
      if (!response.ok || data.error) throw new Error(data.error ?? `HTTP ${response.status}`);
      setProjectTrust(data);
      setProjectTrustDialogOpen(false);
      setModelsRefreshKey((key) => key + 1);
      setSessionKey((key) => key + 1);
    } catch (error) {
      setProjectTrustError(error instanceof Error ? error.message : String(error));
    } finally {
      setProjectTrustBusy(false);
    }
  }, [projectTrustBusy, projectTrustCwd]);

  const activeCwdName = activeCwd ? activeCwd.split(/[\\/]/).filter(Boolean).pop() || activeCwd : null;
  const windowTitle = activeCwdName ? `${activeCwdName} - PolyKit Agent` : "PolyKit Agent";

  useEffect(() => {
    const syncWindowTitle = () => {
      if (document.title !== windowTitle) document.title = windowTitle;
    };

    syncWindowTitle();
    const observer = new MutationObserver(syncWindowTitle);
    observer.observe(document.head, { childList: true, subtree: true, characterData: true });
    return () => observer.disconnect();
  }, [windowTitle]);

  const sidebarContent = (
    <>
      <SessionSidebar
        selectedSessionId={selectedSession?.id ?? null}
        onSelectSession={handleSelectSession}
        onNewSession={handleNewSession}
        initialSessionId={initialSessionId}
        skipInitialProjectSelection={initialNavigation.requestedCwd !== null}
        onInitialRestoreDone={handleInitialRestoreDone}
        refreshKey={refreshKey}
        onSessionArchived={handleSessionDeleted}
        selectedCwd={selectedSession?.cwd ?? newSessionCwd ?? null}
        onCwdChange={handleCwdChange}
        onCollapseSidebar={handleSidebarToggle}
      />
      <div style={{ padding: "8px", flexShrink: 0, display: "flex", flexDirection: "column", gap: 4, borderTop: "1px solid color-mix(in srgb, var(--border) 70%, transparent)" }}>
        <button
          type="button"
          onClick={(e) => {
            const rect = e.currentTarget.getBoundingClientRect();
            toggleTheme({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
          }}
          title={isDark ? translate("theme.light") : translate("theme.dark")}
          aria-label={isDark ? translate("theme.light") : translate("theme.dark")}
          aria-pressed={isDark}
          style={{
            width: "100%", height: 34, display: "flex", alignItems: "center", justifyContent: "flex-start", gap: 8,
            padding: "0 10px", background: "none", border: "none",
            borderRadius: 8, color: "var(--text-muted)", cursor: "pointer",
            fontSize: 12, textAlign: "left", transition: "background 0.12s, color 0.12s",
          }}
          onMouseEnter={(e) => { e.currentTarget.style.background = "var(--bg-hover)"; e.currentTarget.style.color = "var(--text)"; }}
          onMouseLeave={(e) => { e.currentTarget.style.background = "none"; e.currentTarget.style.color = "var(--text-muted)"; }}
        >
          {isDark ? <Moon size={15} strokeWidth={1.8} /> : <Sun size={15} strokeWidth={1.8} />}
          {isDark ? translate("theme.darkName") : translate("theme.lightName")}
        </button>
        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
          <button
            type="button"
            onClick={() => setSettingsOpen(true)}
            title={translate("common.settings")}
            style={{
              flex: 1, display: "flex", alignItems: "center", justifyContent: "flex-start", gap: 8,
              height: 34, padding: "0 10px", background: "none", border: "none",
              borderRadius: 8, color: "var(--text-muted)", cursor: "pointer",
              fontSize: 12, transition: "background 0.12s, color 0.12s",
            }}
            onMouseEnter={(e) => { e.currentTarget.style.background = "var(--bg-hover)"; e.currentTarget.style.color = "var(--text)"; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = "none"; e.currentTarget.style.color = "var(--text-muted)"; }}
          >
            <Settings size={15} strokeWidth={1.8} />
            {translate("common.settings")}
          </button>
          <button
            type="button"
            onClick={() => setHelpOpen(true)}
            title={translate("common.help")}
            aria-label={translate("common.help")}
            style={{
              display: "flex", alignItems: "center", justifyContent: "center",
              width: 34, height: 34, padding: 0, background: "none", border: "none",
              borderRadius: 8, color: "var(--text-muted)", cursor: "pointer", flexShrink: 0,
              transition: "background 0.12s, color 0.12s",
            }}
            onMouseEnter={(e) => { e.currentTarget.style.background = "var(--bg-hover)"; e.currentTarget.style.color = "var(--text)"; }}
            onMouseLeave={(e) => { e.currentTarget.style.background = "none"; e.currentTarget.style.color = "var(--text-muted)"; }}
          >
            <CircleHelp size={16} strokeWidth={1.8} />
          </button>
      </div>
      </div>
    </>
  );

  const sidebarRailButtonStyle: React.CSSProperties = {
    width: 42,
    height: 42,
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    padding: 0,
    border: "none",
    borderRadius: 10,
    background: "transparent",
    color: "var(--text)",
    cursor: "pointer",
    flexShrink: 0,
  };

  return (
    <>
    <style>{`
      @media (max-width: 640px) {
        .sidebar-overlay-backdrop.sidebar-mobile-pending {
          opacity: 0 !important;
          pointer-events: none !important;
        }
        .sidebar-container.sidebar-mobile-pending.sidebar-open {
          transform: translateX(calc(-100% - env(safe-area-inset-left)));
          box-shadow: none;
        }
      }
    `}</style>
    <div style={{
      display: "flex",
      width: "100%",
      height: "var(--app-viewport-height, 100dvh)",
      paddingLeft: "env(safe-area-inset-left)",
      paddingRight: "env(safe-area-inset-right)",
      overflow: "hidden",
      background: "var(--bg)",
    }}>
      {/* Mobile overlay backdrop */}
      <div
        className={`sidebar-overlay-backdrop${mobileSidebarReady ? "" : " sidebar-mobile-pending"}`}
        onClick={() => setSidebarOpen(false)}
        style={{
          position: "fixed",
          inset: 0,
          zIndex: 199,
          background: "rgba(0,0,0,0.4)",
          opacity: sidebarOpen ? 1 : 0,
          pointerEvents: sidebarOpen ? "auto" : "none",
          transition: "opacity 0.25s ease",
        }}
      />

      {/* Left sidebar */}
      <div
        ref={sidebarResizer.panelRef}
        id="session-sidebar"
        className={`sidebar-container${sidebarOpen ? " sidebar-open" : " sidebar-closed"}${mobileSidebarReady ? "" : " sidebar-mobile-pending"}${sidebarResizer.isResizing ? " sidebar-resizing" : ""}`}
        style={{
          "--sidebar-width": `${sidebarResizer.width}px`,
          background: "var(--bg-panel)",
          borderRight: "1px solid var(--border)",
          display: "flex",
          flexDirection: "column",
          flexShrink: 0,
          paddingTop: "env(safe-area-inset-top)",
          paddingBottom: "env(safe-area-inset-bottom)",
          zIndex: 200,
        } as React.CSSProperties}
      >
        {sidebarContent}
      </div>
      {sidebarOpen && (
        <div
          {...sidebarResizer.separatorProps}
          aria-controls="session-sidebar"
          className={`panel-resize-handle sidebar-resize-handle${sidebarResizer.isResizing ? " is-resizing" : ""}`}
          data-resize-handle="sidebar"
          title={`${translate("layout.resizeSidebar")}: ${translate("layout.resizeHint")}`}
        />
      )}

      {!sidebarOpen && !isMobile && (
        <aside
          className="sidebar-collapsed-rail"
          aria-label={translate("sidebar.show")}
          style={{
            width: 68,
            minWidth: 68,
            height: "100%",
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            gap: 14,
            padding: "22px 0 20px",
            flexShrink: 0,
          }}
        >
          <button type="button" className="sidebar-rail-brand" style={sidebarRailButtonStyle} onClick={() => setSidebarOpen(true)} title={translate("sidebar.show")} aria-label={translate("sidebar.show")}><span style={{ width: 28, height: 28, display: "inline-flex", alignItems: "center", justifyContent: "center", borderRadius: 8, background: "var(--text)", color: "var(--bg-panel)", fontFamily: "var(--font-mono)", fontSize: 15, fontWeight: 700 }}>&gt;</span></button>
          <button type="button" style={sidebarRailButtonStyle} onClick={() => activeCwd ? handleNewSession(`rail-${Date.now()}`, activeCwd) : setSidebarOpen(true)} title={translate("sidebar.new")} aria-label={translate("sidebar.new")}><PlusCircle size={21} strokeWidth={1.8} /></button>
          <button type="button" style={sidebarRailButtonStyle} onClick={() => { setSidebarOpen(true); window.setTimeout(() => window.dispatchEvent(new Event("polykit-agent:choose-project")), 0); }} title={translate("sidebar.customPath")} aria-label={translate("sidebar.customPath")}><FolderPlus size={21} strokeWidth={1.8} /></button>
          <span className="sidebar-rail-spacer" style={{ flex: 1 }} />
          <button
            type="button"
            style={sidebarRailButtonStyle}
            onClick={(e) => {
              const rect = e.currentTarget.getBoundingClientRect();
              toggleTheme({ x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
            }}
            title={isDark ? translate("theme.light") : translate("theme.dark")}
            aria-label={isDark ? translate("theme.light") : translate("theme.dark")}
            aria-pressed={isDark}
          >
            {isDark ? <Moon size={21} strokeWidth={1.8} /> : <Sun size={21} strokeWidth={1.8} />}
          </button>
          <button type="button" style={sidebarRailButtonStyle} onClick={() => setSettingsOpen(true)} title={translate("common.settings")} aria-label={translate("common.settings")}><Settings size={21} strokeWidth={1.8} /></button>
        </aside>
      )}

      {!sidebarOpen && isMobile && !showChat && (
        <button
          type="button"
          className="sidebar-reopen-button"
          onClick={() => setSidebarOpen(true)}
          title={translate("sidebar.show")}
          aria-label={translate("sidebar.show")}
          aria-controls="session-sidebar"
        >
          <PanelLeftOpen size={18} strokeWidth={1.8} />
        </button>
      )}

      {/* Center: chat */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minWidth: 0 }}>
        {isMobile && showChat && (
          <div
            style={{
              height: "calc(44px + env(safe-area-inset-top))",
              paddingTop: "env(safe-area-inset-top)",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              flexShrink: 0,
              borderBottom: "1px solid var(--border)",
              background: "var(--bg-panel)",
            }}
          >
            <button
              type="button"
              onClick={handleSidebarToggle}
              title={sidebarOpen ? translate("sidebar.hide") : translate("sidebar.show")}
              aria-label={sidebarOpen ? translate("sidebar.hide") : translate("sidebar.show")}
              style={{
                width: 44,
                height: 44,
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                padding: 0,
                border: "none",
                background: "transparent",
                color: "var(--text-muted)",
              }}
            >
              <PanelLeftOpen size={19} strokeWidth={1.8} />
            </button>
            {projectTrust?.requiresTrust && !projectTrust.trusted && (
              <button
                type="button"
                onClick={() => {
                  setProjectTrustError(null);
                  setProjectTrustDialogOpen(true);
                }}
                title={translate("trust.resourcesNotLoaded")}
                aria-label={translate("trust.resourcesNotLoaded")}
                style={{
                  width: 44,
                  height: 44,
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  padding: 0,
                  border: "none",
                  background: "transparent",
                  color: "#d97706",
                }}
              >
                <ShieldAlert size={18} strokeWidth={1.8} />
              </button>
            )}
          </div>
        )}

        {/* Chat content */}
        <div style={{ flex: 1, overflow: "hidden", position: "relative" }}>
          {showChat && !isMobile && projectTrust?.requiresTrust && !projectTrust.trusted && (
            <button
              type="button"
              className="workspace-trust-button"
              onClick={() => {
                setProjectTrustError(null);
                setProjectTrustDialogOpen(true);
              }}
              title={translate("trust.resourcesNotLoaded")}
              aria-label={translate("trust.resourcesNotLoaded")}
            >
              <ShieldAlert size={16} strokeWidth={1.8} />
              {!isMobile && <span>{translate("trust.resourcesNotLoaded")}</span>}
            </button>
          )}
          {showChat ? (
            <ChatWindow
              key={sessionKey}
              session={selectedSession}
              newSessionCwd={effectiveNewSessionCwd}
              onAgentEnd={handleAgentEnd}
              onSessionCreated={handleSessionCreated}
              onSessionForked={handleSessionForked}
              modelsRefreshKey={modelsRefreshKey}
              chatInputRef={chatInputRef}
              onChooseProject={() => {
                setSidebarOpen(true);
                window.setTimeout(() => window.dispatchEvent(new Event("polykit-agent:choose-project")), 0);
              }}
            />
          ) : initialCwdStatus === "validating" ? (
            <div
              role="status"
              style={{ height: "100%", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 8, padding: 24, color: "var(--text-muted)", textAlign: "center" }}
            >
               <div style={{ fontSize: 14, color: "var(--text)" }}>{translate("workspace.opening")}</div>
              <div style={{ maxWidth: "min(720px, 100%)", overflowWrap: "anywhere", fontFamily: "var(--font-mono)", fontSize: 12 }}>
                {initialNavigation.requestedCwd}
              </div>
            </div>
          ) : initialCwdStatus === "error" ? (
            <div
              role="alert"
              style={{ height: "100%", display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", gap: 8, padding: 24, color: "var(--text-muted)", textAlign: "center" }}
            >
               <div style={{ fontSize: 14, color: "#dc2626" }}>{translate("workspace.unable")}</div>
              <div style={{ maxWidth: "min(720px, 100%)", overflowWrap: "anywhere", fontFamily: "var(--font-mono)", fontSize: 12 }}>
                {initialNavigation.requestedCwd}
              </div>
              <div style={{ maxWidth: 720, fontSize: 12 }}>{initialCwdError}</div>
              <button
                type="button"
                style={{
                  marginTop: 10,
                  minHeight: 34,
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 7,
                  padding: "0 12px",
                  border: "1px solid var(--border)",
                  borderRadius: 8,
                  background: "var(--surface)",
                  color: "var(--text-muted)",
                  fontSize: 13,
                  cursor: "pointer",
                }}
                onClick={() => {
                  setSidebarOpen(true);
                  window.setTimeout(() => window.dispatchEvent(new Event("polykit-agent:choose-project")), 0);
                }}
              >
                <FolderPlus size={17} strokeWidth={1.8} />
                {translate("sidebar.selectProject")}
              </button>
            </div>
          ) : showPlaceholder ? (
            <div className="workspace-empty-state">
              <div className="workspace-empty-new-chat">
                <h1>{translate("welcome.greeting")}</h1>
                <button type="button" onClick={() => {
                  setSidebarOpen(true);
                  window.setTimeout(() => window.dispatchEvent(new Event("polykit-agent:choose-project")), 0);
                }}>
                  <FolderPlus size={17} strokeWidth={1.8} />
                  {translate("sidebar.selectProject")}
                </button>
              </div>
            </div>
          ) : null}
        </div>
      </div>

    </div>
    {settingsOpen && (
      <SettingsModal
        cwd={projectTrustCwd}
        sessionId={selectedSession?.id ?? null}
        onClose={() => setSettingsOpen(false)}
        onModelsRefresh={() => setModelsRefreshKey((k) => k + 1)}
        onPluginsReloaded={() => setSessionKey((k) => k + 1)}
        onAgentConfigureMcp={handleAgentConfigureMcp}
        onSessionDeleted={handleSessionDeleted}
        onSessionsChanged={() => setRefreshKey((k) => k + 1)}
      />
    )}
    {helpOpen && <HelpModal onClose={() => setHelpOpen(false)} />}
    {projectTrustDialogOpen && projectTrustCwd && (
      <ProjectTrustDialog
        cwd={projectTrustCwd}
        busy={projectTrustBusy}
        error={projectTrustError}
        onCancel={() => {
          if (!projectTrustBusy) setProjectTrustDialogOpen(false);
        }}
        onConfirm={() => void handleTrustProject()}
      />
    )}

    </>
  );
}
