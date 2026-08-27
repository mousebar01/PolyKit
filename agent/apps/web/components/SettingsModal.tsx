"use client";

import { useEffect, useRef, useState } from "react";
import { useI18n } from "@/hooks/useI18n";
import { useIsMobile } from "@/hooks/useIsMobile";
import { useTheme } from "@/hooks/useTheme";
import { ModelsConfig } from "./ModelsConfig";
import { SkillsConfig } from "./SkillsConfig";
import { PluginsConfig } from "./PluginsConfig";
import { McpConfig } from "./McpConfig";
import { MobileDevicesConfig } from "./MobileDevicesConfig";
import { ArchivedSessionsConfig } from "./ArchivedSessionsConfig";
import { HiddenWorkspacesConfig } from "./HiddenWorkspacesConfig";
import { Archive, Check, EyeOff, Moon, Plug, Smartphone, Sun, X } from "lucide-react";

type SettingsSection = "general" | "archives" | "workspaces" | "mobile" | "models" | "skills" | "plugins" | "mcp";

const SECTION_STORAGE_KEY = "pi-settings-section";
const SECTION_IDS: SettingsSection[] = ["general", "archives", "workspaces", "mobile", "models", "skills", "plugins", "mcp"];

interface Props {
  cwd: string | null;
  sessionId: string | null;
  onClose: () => void;
  onModelsRefresh: () => void;
  onPluginsReloaded: () => void;
  onAgentConfigureMcp: (serverName?: string) => void;
  onSessionDeleted?: (sessionId: string) => void;
  onSessionsChanged?: () => void;
}

function SectionIcon({ section }: { section: SettingsSection }) {
  const common = {
    width: 14,
    height: 14,
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 2,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
  };
  switch (section) {
    case "general":
      return (
        <svg {...common}>
          <circle cx="12" cy="12" r="3" />
          <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
        </svg>
      );
    case "archives":
      return <Archive {...common} />;
    case "workspaces":
      return <EyeOff {...common} />;
    case "models":
      return (
        <svg {...common}>
          <rect x="4" y="4" width="16" height="16" rx="2" /><rect x="9" y="9" width="6" height="6" />
          <line x1="9" y1="1" x2="9" y2="4" /><line x1="15" y1="1" x2="15" y2="4" />
          <line x1="9" y1="20" x2="9" y2="23" /><line x1="15" y1="20" x2="15" y2="23" />
          <line x1="20" y1="9" x2="23" y2="9" /><line x1="20" y1="14" x2="23" y2="14" />
          <line x1="1" y1="9" x2="4" y2="9" /><line x1="1" y1="14" x2="4" y2="14" />
        </svg>
      );
    case "mobile":
      return <Smartphone {...common} />;
    case "skills":
      return (
        <svg {...common}>
          <path d="M12 2L2 7l10 5 10-5-10-5z" />
          <path d="M2 17l10 5 10-5" />
          <path d="M2 12l10 5 10-5" />
        </svg>
      );
    case "plugins":
      return (
        <svg {...common}>
          <path d="M9 7V2" />
          <path d="M15 7V2" />
          <path d="M6 13V8a1 1 0 0 1 1-1h10a1 1 0 0 1 1 1v5a6 6 0 0 1-12 0Z" />
          <path d="M12 19v3" />
        </svg>
      );
    case "mcp":
      return <Plug {...common} />;
  }
}

export function SettingsModal({ cwd, sessionId, onClose, onModelsRefresh, onPluginsReloaded, onAgentConfigureMcp, onSessionDeleted, onSessionsChanged }: Props) {
  const { t, locale, setLocale, supportedLocales } = useI18n();
  const { isDark, toggleTheme } = useTheme();
  const isMobile = useIsMobile();
  const [section, setSection] = useState<SettingsSection>(() => {
    try {
      const saved = window.localStorage.getItem(SECTION_STORAGE_KEY);
      if (SECTION_IDS.includes(saved as SettingsSection)) return saved as SettingsSection;
    } catch { /* storage unavailable */ }
    return "general";
  });
  const visitedModelsRef = useRef(false);

  useEffect(() => {
    try {
      window.localStorage.setItem(SECTION_STORAGE_KEY, section);
    } catch { /* storage unavailable */ }
  }, [section]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const handleClose = () => {
    // Models config can change auth/model state, so refresh the model list
    // if the user ever opened that section (same as the old modal behavior).
    if (visitedModelsRef.current) onModelsRefresh();
    onClose();
  };

  const selectSection = (next: SettingsSection) => {
    if (next === "models") visitedModelsRef.current = true;
    setSection(next);
  };

  const labelFor = (id: SettingsSection): string => {
    switch (id) {
      case "general": return t("common.general");
      case "archives": return t("common.archived");
      case "workspaces": return t("common.hiddenWorkspaces");
      case "models": return t("common.models");
      case "mobile": return t("common.mobileDevices");
      case "skills": return t("common.skills");
      case "plugins": return t("common.plugins");
      case "mcp": return "MCP";
    }
  };

  return (
    <div
      className="settings-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) handleClose();
      }}
    >
      <div
        className="settings-dialog"
        style={isMobile ? { width: "calc(100vw - 16px)", height: "calc(100dvh - 16px)" } : undefined}
      >
        {/* Header */}
        <div className="settings-header">
          <span className="settings-title">
            {t("common.settings")}
          </span>
          <button
            type="button"
            onClick={handleClose}
            aria-label={t("i18n.close")}
            className="settings-close-button"
          >
            <X size={17} strokeWidth={1.8} />
          </button>
        </div>

        {/* Body */}
        <div className={`settings-body${isMobile ? " is-mobile" : ""}`}>
          {/* Section nav */}
          <div className="settings-nav">
            {SECTION_IDS.map((id) => {
              const active = id === section;
              return (
                <button
                  key={id}
                  type="button"
                  onClick={() => selectSection(id)}
                  aria-current={active ? "true" : undefined}
                  className={`settings-nav-item${active ? " is-active" : ""}`}
                >
                  <SectionIcon section={id} />
                  {labelFor(id)}
                </button>
              );
            })}
          </div>

          {/* Section content */}
          <div className="settings-content">
            {section === "general" && (
              <div className="settings-general">
                <div className="settings-section-heading">
                  <h2>{t("common.general")}</h2>
                </div>
                {/* Language */}
                <section className="settings-group" aria-labelledby="settings-language-label">
                  <div className="settings-row">
                    <div className="settings-row-label" id="settings-language-label">{t("common.language")}</div>
                    <div className="settings-segmented" role="radiogroup" aria-labelledby="settings-language-label">
                    {supportedLocales.map((plugin) => (
                      <button
                        key={plugin.id}
                        type="button"
                        onClick={() => setLocale(plugin.id as typeof locale)}
                        role="radio"
                        aria-checked={locale === plugin.id}
                        className={`settings-segment${locale === plugin.id ? " is-selected" : ""}`}
                      >
                        <span>{plugin.label}</span>
                        {locale === plugin.id && (
                          <Check size={14} strokeWidth={2.2} aria-hidden="true" />
                        )}
                      </button>
                    ))}
                    </div>
                  </div>
                </section>

                {/* Theme */}
                <section className="settings-group" aria-labelledby="settings-theme-label">
                  <div className="settings-row">
                    <div className="settings-row-label" id="settings-theme-label">{t("common.theme")}</div>
                    <div className="settings-segmented" role="radiogroup" aria-labelledby="settings-theme-label">
                      <button type="button" role="radio" aria-checked={!isDark} className={`settings-segment${!isDark ? " is-selected" : ""}`} onClick={(event) => { if (isDark) toggleTheme({ x: event.clientX, y: event.clientY }); }}>
                        <Sun size={15} strokeWidth={1.8} />
                        <span>{t("theme.lightName")}</span>
                      </button>
                      <button type="button" role="radio" aria-checked={isDark} className={`settings-segment${isDark ? " is-selected" : ""}`} onClick={(event) => { if (!isDark) toggleTheme({ x: event.clientX, y: event.clientY }); }}>
                        <Moon size={15} strokeWidth={1.8} />
                        <span>{t("theme.darkName")}</span>
                      </button>
                    </div>
                  </div>
                </section>
              </div>
            )}

            {section === "archives" && <ArchivedSessionsConfig onSessionDeleted={onSessionDeleted} onSessionsChanged={onSessionsChanged} />}

            {section === "workspaces" && <HiddenWorkspacesConfig onChanged={onSessionsChanged} />}

            {section === "models" && (
              <ModelsConfig embedded onClose={handleClose} />
            )}

            {section === "mobile" && <MobileDevicesConfig />}

            {section === "skills" && (
              cwd
                ? <SkillsConfig embedded cwd={cwd} onClose={handleClose} />
                : (
                  <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", padding: 24, fontSize: 12, color: "var(--text-dim)", textAlign: "center" }}>
                    {t("settings.needProject")}
                  </div>
                )
            )}

            {section === "plugins" && (
              cwd
                ? (
                  <PluginsConfig
                    embedded
                    cwd={cwd}
                    sessionId={sessionId}
                    onClose={handleClose}
                    onReloaded={onPluginsReloaded}
                  />
                )
                : (
                  <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", padding: 24, fontSize: 12, color: "var(--text-dim)", textAlign: "center" }}>
                    {t("settings.needProject")}
                  </div>
                )
            )}

            {section === "mcp" && (
              cwd
                ? <McpConfig cwd={cwd} sessionId={sessionId} onAgentConfigure={onAgentConfigureMcp} onReloaded={onPluginsReloaded} />
                : (
                  <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", padding: 24, fontSize: 12, color: "var(--text-dim)", textAlign: "center" }}>
                    {t("settings.needProject")}
                  </div>
                )
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
