import { existsSync, mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { getAgentDir } from "@earendil-works/pi-coding-agent";
import { writePrivateFileAtomicSync } from "./atomic-file";

const ARCHIVE_FILE_NAME = "polykit-agent-session-state.json";
const ARCHIVE_STATE_VERSION = 1;

export interface SessionArchiveRecord {
  archivedAt: string;
}

export interface HiddenWorkspaceRecord {
  hiddenAt: string;
}

interface SessionArchiveState {
  version: number;
  archived: Record<string, SessionArchiveRecord>;
  hiddenWorkspaces: Record<string, HiddenWorkspaceRecord>;
}

function getArchivePath(): string {
  return join(getAgentDir(), ARCHIVE_FILE_NAME);
}

function emptyState(): SessionArchiveState {
  return { version: ARCHIVE_STATE_VERSION, archived: {}, hiddenWorkspaces: {} };
}

function readState(): SessionArchiveState {
  const path = getArchivePath();
  if (!existsSync(path)) return emptyState();

  try {
    const parsed = JSON.parse(readFileSync(path, "utf8")) as Partial<SessionArchiveState>;
    if (!parsed || typeof parsed !== "object" || !parsed.archived || typeof parsed.archived !== "object") {
      return emptyState();
    }

    const archived: Record<string, SessionArchiveRecord> = {};
    for (const [sessionId, record] of Object.entries(parsed.archived)) {
      if (!record || typeof record !== "object") continue;
      const archivedAt = (record as Partial<SessionArchiveRecord>).archivedAt;
      if (typeof archivedAt === "string" && archivedAt.length > 0) {
        archived[sessionId] = { archivedAt };
      }
    }

    const hiddenWorkspaces: Record<string, HiddenWorkspaceRecord> = {};
    const storedHiddenWorkspaces = parsed.hiddenWorkspaces;
    if (storedHiddenWorkspaces && typeof storedHiddenWorkspaces === "object") {
      for (const [root, record] of Object.entries(storedHiddenWorkspaces)) {
        if (!record || typeof record !== "object") continue;
        const hiddenAt = (record as Partial<HiddenWorkspaceRecord>).hiddenAt;
        if (typeof hiddenAt === "string" && hiddenAt.length > 0) {
          hiddenWorkspaces[root] = { hiddenAt };
        }
      }
    }

    return { version: ARCHIVE_STATE_VERSION, archived, hiddenWorkspaces };
  } catch {
    return emptyState();
  }
}

function writeState(state: SessionArchiveState): void {
  const agentDir = getAgentDir();
  if (!existsSync(agentDir)) mkdirSync(agentDir, { recursive: true, mode: 0o700 });
  writePrivateFileAtomicSync(getArchivePath(), `${JSON.stringify(state, null, 2)}\n`);
}

export function getSessionArchiveRecords(): Record<string, SessionArchiveRecord> {
  return readState().archived;
}

export function getHiddenWorkspaceRecords(): Record<string, HiddenWorkspaceRecord> {
  return readState().hiddenWorkspaces;
}

export function setSessionsArchived(sessionIds: Iterable<string>, archived: boolean): void {
  const state = readState();
  const ids = [...new Set(sessionIds)].filter(Boolean);
  const archivedAt = new Date().toISOString();

  for (const sessionId of ids) {
    if (archived) state.archived[sessionId] = { archivedAt };
    else delete state.archived[sessionId];
  }

  writeState(state);
}

export function removeSessionArchiveRecord(sessionId: string): void {
  const state = readState();
  if (!(sessionId in state.archived)) return;
  delete state.archived[sessionId];
  writeState(state);
}

export function setWorkspaceHidden(root: string, hidden: boolean): void {
  const state = readState();
  if (hidden) state.hiddenWorkspaces[root] = { hiddenAt: new Date().toISOString() };
  else delete state.hiddenWorkspaces[root];
  writeState(state);
}
