"use client";

import { useCallback, useEffect, useState } from "react";
import { EyeOff, RotateCcw } from "lucide-react";
import { useI18n } from "@/hooks/useI18n";
import { agentApiPath } from "@/lib/agent-client";

interface HiddenWorkspace {
  root: string;
  hiddenAt: string;
}

interface Props {
  onChanged?: () => void;
}

export function HiddenWorkspacesConfig({ onChanged }: Props) {
  const { t, locale } = useI18n();
  const [workspaces, setWorkspaces] = useState<HiddenWorkspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyRoot, setBusyRoot] = useState<string | null>(null);

  const loadWorkspaces = useCallback(async () => {
    setLoading(true);
    try {
      const response = await fetch(agentApiPath("/workspaces"), { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json() as { workspaces?: HiddenWorkspace[] };
      setWorkspaces(data.workspaces ?? []);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadWorkspaces();
  }, [loadWorkspaces]);

  const restoreWorkspace = useCallback(async (root: string) => {
    setBusyRoot(root);
    try {
      const response = await fetch(agentApiPath("/workspaces"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ root, hidden: false }),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      onChanged?.();
      await loadWorkspaces();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyRoot(null);
    }
  }, [loadWorkspaces, onChanged]);

  return (
    <div className="hidden-workspaces-config">
      <div className="settings-section-heading">
        <h2>{t("common.hiddenWorkspaces")}</h2>
        <p>{t("settings.hiddenWorkspacesDescription")}</p>
      </div>

      {loading ? (
        <div className="hidden-workspaces-status">{t("settings.hiddenWorkspacesLoading")}</div>
      ) : error ? (
        <div className="hidden-workspaces-status is-error">{error}</div>
      ) : workspaces.length === 0 ? (
        <div className="hidden-workspaces-empty">
          <EyeOff size={24} strokeWidth={1.5} />
          <span>{t("settings.hiddenWorkspacesEmpty")}</span>
        </div>
      ) : (
        <div className="hidden-workspaces-list">
          <div className="hidden-workspaces-list-heading">
            {t("settings.hiddenWorkspacesCount", { count: workspaces.length })}
          </div>
          {workspaces.map((workspace) => {
            const isBusy = busyRoot === workspace.root;
            return (
              <div className="hidden-workspace-item" key={workspace.root}>
                <div className="hidden-workspace-copy">
                  <strong title={workspace.root}>{workspace.root}</strong>
                  <span>
                    {workspace.hiddenAt
                      ? new Date(workspace.hiddenAt).toLocaleString(locale, { dateStyle: "medium", timeStyle: "short" })
                      : t("settings.hiddenWorkspacesUnknownDate")}
                  </span>
                </div>
                <button
                  type="button"
                  onClick={() => void restoreWorkspace(workspace.root)}
                  disabled={isBusy}
                  title={t("settings.restoreWorkspace")}
                  aria-label={t("settings.restoreWorkspace")}
                >
                  <RotateCcw size={14} strokeWidth={1.9} />
                  <span>{isBusy ? t("settings.restoringWorkspace") : t("settings.restoreWorkspace")}</span>
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
