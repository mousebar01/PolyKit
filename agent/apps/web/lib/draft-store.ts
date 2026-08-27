import type { ConversationAnnotation } from "./conversation-annotations";

export interface ChatDraftImage {
  data: string;
  mimeType: string;
}

export interface ChatDraft {
  value: string;
  images: ChatDraftImage[];
  annotations: ConversationAnnotation[];
}

const drafts = new Map<string, ChatDraft>();

function cloneDraft(draft: ChatDraft): ChatDraft {
  return {
    value: draft.value,
    images: draft.images.map((image) => ({ ...image })),
    annotations: draft.annotations.map((annotation) => ({ ...annotation })),
  };
}

function isEmptyDraft(draft: ChatDraft): boolean {
  return !draft.value && draft.images.length === 0 && draft.annotations.length === 0;
}

export function getDraft(key: string): ChatDraft | null {
  const draft = drafts.get(key);
  return draft ? cloneDraft(draft) : null;
}

export function setDraft(key: string, draft: ChatDraft): void {
  if (isEmptyDraft(draft)) {
    drafts.delete(key);
    return;
  }
  drafts.set(key, cloneDraft(draft));
}

export function clearDraft(key: string): void {
  drafts.delete(key);
}
