export type AnnotationSourceRole = "user" | "assistant";

export interface ConversationAnnotation {
  id: string;
  quote: string;
  comment: string;
  sourceRole?: AnnotationSourceRole;
  sourceEntryId?: string;
  /** Text offsets inside the rendered source message, used for draft highlighting. */
  sourceStartOffset?: number;
  sourceEndOffset?: number;
}

const ANNOTATION_START = "<!-- pi:conversation-annotations -->";
const ANNOTATION_END = "<!-- /pi:conversation-annotations -->";

function normalizeQuote(value: string): string {
  return value.replace(/\r\n/g, "\n").trim();
}

function quoteAsMarkdown(value: string): string {
  return normalizeQuote(value)
    .split("\n")
    .map((line) => `> ${line}`)
    .join("\n");
}

function unquoteMarkdown(value: string): string {
  return value
    .split("\n")
    .map((line) => line.startsWith("> ") ? line.slice(2) : line === ">" ? "" : line)
    .join("\n")
    .trim();
}

function annotationId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `annotation-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

export function createConversationAnnotation(
  values: Omit<ConversationAnnotation, "id"> & { id?: string },
): ConversationAnnotation {
  return {
    ...values,
    id: values.id || annotationId(),
    quote: normalizeQuote(values.quote),
    comment: values.comment.trim(),
  };
}

/**
 * Keep annotations in the actual prompt so the agent receives the selected
 * context and the session file remains auditable after a reload. The HTML
 * comments are structural delimiters; the content between them is deliberately
 * plain Markdown so it is useful to the model even if a different renderer is
 * used later.
 */
export function serializeAnnotatedMessage(
  message: string,
  annotations: ConversationAnnotation[],
): string {
  const normalizedMessage = message.trim();
  if (annotations.length === 0) return normalizedMessage;

  const body = annotations.map((annotation, index) => {
    const comment = annotation.comment.trim() || "(仅引用这段内容，请结合上下文进行说明。)";
    return [
      `[Conversation annotation ${index + 1}]`,
      "[Selected text]",
      quoteAsMarkdown(annotation.quote),
      "[Comment]",
      comment,
      `[/Conversation annotation ${index + 1}]`,
    ].join("\n");
  }).join("\n\n");

  const prefix = normalizedMessage ? `${normalizedMessage}\n\n` : "";
  return `${prefix}${ANNOTATION_START}\n${body}\n${ANNOTATION_END}`;
}

export function parseAnnotatedMessage(message: string): {
  text: string;
  annotations: ConversationAnnotation[];
} {
  const match = message.match(new RegExp(`${escapeRegExp(ANNOTATION_START)}\\s*([\\s\\S]*?)\\s*${escapeRegExp(ANNOTATION_END)}`));
  if (!match) return { text: message, annotations: [] };

  const annotations: ConversationAnnotation[] = [];
  const blockPattern = /\[Conversation annotation (\d+)\]\s*\[Selected text\]\s*([\s\S]*?)\s*\[Comment\]\s*([\s\S]*?)\s*\[\/Conversation annotation \1\]/g;
  let blockMatch: RegExpExecArray | null;
  while ((blockMatch = blockPattern.exec(match[1])) !== null) {
    const quote = unquoteMarkdown(blockMatch[2]);
    if (!quote) continue;
    annotations.push({
      id: `history-${annotations.length + 1}-${quote.slice(0, 24)}`,
      quote,
      comment: blockMatch[3].trim().replace(/^\(仅引用这段内容，请结合上下文进行说明。\)$/, ""),
    });
  }

  return {
    text: message.replace(match[0], "").trim(),
    annotations,
  };
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
