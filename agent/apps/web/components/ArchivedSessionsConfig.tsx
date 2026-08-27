"use client";

import { useCallback, useEffect, useState } from "react";
import { ArchiveRestore, RotateCcw, Trash2 } from "lucide-react";
import type { SessionInfo } from "@/lib/types";
import { useI18n } from "@/hooks/useI18n";
import { agentApiPath } from "@/lib/agent-client";

interface Props {
  onSessionDeleted?: (sessionId: string) => void;
  onSessionsChanged?: () => void;
}

function sessionTitle(session: SessionInfo): string {
  return session.name || session.firstMessage.slice(0, 72) || session.id.slice(0, 12);
}

export function ArchivedSessionsConfig({ onSessionDeleted, onSessionsChanged }: Props) {
  const { t, locale } = useI18n();
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [deleteCandidate, setDeleteCandidate] = useState<string | null>(null);

  const loadSessions = useCallback(async () => {
    setLoading(true);
    try {
      const response = await fetch(`${agentApiPath("/sessions")}?includeArchived=1`, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json() as { sessions?: SessionInfo[] };
      setSessions((data.sessions ?? []).filter((session) => session.archived));
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  const handleRestore = useCallback(async (session: SessionInfo) => {
    setBusyId(session.id);
    try {
      const response = await fetch(agentApiPath(`/sessions/${encodeURIComponent(session.id)}/restore`), { method: "POST" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      onSessionsChanged?.();
      await loadSessions();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyId(null);
    }
  }, [loadSessions, onSessionsChanged]);

  const handleDelete = useCallback(async (session: SessionInfo) => {
    setBusyId(session.id);
    try {
      const response = await fetch(agentApiPath(`/sessions/${encodeURIComponent(session.id)}`), { method: "DELETE" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setDeleteCandidate(null);
      onSessionDeleted?.(session.id);
      onSessionsChanged?.();
      await loadSessions();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusyId(null);
    }
  }, [loadSessions, onSessionDeleted, onSessionsChanged]);

  return (
    <div className="archived-sessions-config">
      <div className="settings-section-heading">
        <h2>{t("common.archived")}</h2>
        <p>{t("settings.archivedDescription")}</p>
      </div>

      {loading ? (
        <div className="archived-sessions-status">{t("settings.archivedLoading")}</div>
      ) : error ? (
        <div className="archived-sessions-status is-error">{error}</div>
      ) : sessions.length === 0 ? (
        <div className="archived-sessions-empty">
          <ArchiveRestore size={24} strokeWidth={1.5} />
          <span>{t("settings.archivedEmpty")}</span>
        </div>
      ) : (
        <div className="archived-sessions-list">
          <div className="archived-sessions-list-heading">
            <span>{t("settings.archivedCount", { count: sessions.length })}</span>
          </div>
          {sessions.map((session) => {
            const title = sessionTitle(session);
            const isBusy = busyId === session.id;
            const isDeleting = deleteCandidate === session.id;
            return (
              <div className="archived-session-item" key={session.id}>
                <div className="archived-session-copy">
                  <strong title={title}>{title}</strong>
                  <span>
                    {session.archivedAt
                      ? new Date(session.archivedAt).toLocaleString(locale, { dateStyle: "medium", timeStyle: "short" })
                      : t("settings.archivedUnknownDate")}
                    {session.parentSessionId ? ` · ${t("settings.archivedBranch")}` : ""}
                  </span>
                </div>
                {isDeleting ? (
                  <div className="archived-session-confirm">
                    <span>{t("settings.permanentDeletePrompt")}</span>
                    <button type="button" className="is-danger" onClick={() => void handleDelete(session)} disabled={isBusy}>
                      <Trash2 size={13} strokeWidth={1.9} />
                      {t("settings.permanentDelete")}
                    </button>
                    <button type="button" onClick={() => setDeleteCandidate(null)} disabled={isBusy}>{t("sidebar.cancel")}</button>
                  </div>
                ) : (
                  <div className="archived-session-actions">
                    <button type="button" onClick={() => void handleRestore(session)} disabled={isBusy} title={t("settings.restoreArchived")} aria-label={t("settings.restoreArchived")}>
                      <RotateCcw size={14} strokeWidth={1.9} />
                      <span>{t("settings.restoreArchived")}</span>
                    </button>
                    <button type="button" className="is-danger" onClick={() => setDeleteCandidate(session.id)} disabled={isBusy} title={t("settings.permanentDelete")} aria-label={t("settings.permanentDelete")}>
                      <Trash2 size={14} strokeWidth={1.9} />
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
