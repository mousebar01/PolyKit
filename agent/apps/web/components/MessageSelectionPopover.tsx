"use client";

import { useCallback, useEffect, useRef, useState, type CSSProperties, type RefObject } from "react";
import { Check, MessageSquarePlus, X } from "lucide-react";

export interface MessageSelectionSnapshot {
  quote: string;
  startOffset: number;
  endOffset: number;
  rect: {
    top: number;
    left: number;
    right: number;
    bottom: number;
    width: number;
    height: number;
  };
}

export interface MessageSelectionState {
  rootRef: RefObject<HTMLDivElement | null>;
  selection: MessageSelectionSnapshot | null;
  commentOpen: boolean;
  comment: string;
  setComment: (value: string) => void;
  openComment: () => void;
  submit: () => void;
  cancel: () => void;
  annotationNumber: number;
}

function selectionSnapshot(root: HTMLDivElement): MessageSelectionSnapshot | null {
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) return null;
  if (!root.contains(selection.anchorNode) || !root.contains(selection.focusNode)) return null;
  const contentRoot = root.querySelector<HTMLElement>("[data-annotation-content]") ?? root;
  if (!contentRoot.contains(selection.anchorNode) || !contentRoot.contains(selection.focusNode)) return null;

  const selectedText = selection.toString();
  const quote = selectedText.trim();
  if (!quote) return null;

  const range = selection.getRangeAt(0);
  const prefixRange = document.createRange();
  prefixRange.selectNodeContents(contentRoot);
  prefixRange.setEnd(range.startContainer, range.startOffset);
  const leadingWhitespace = selectedText.length - selectedText.trimStart().length;
  const startOffset = prefixRange.toString().length + leadingWhitespace;
  const rect = range.getBoundingClientRect();
  if (rect.width === 0 && rect.height === 0) return null;
  return {
    quote,
    startOffset,
    endOffset: startOffset + quote.length,
    rect: {
      top: rect.top,
      left: rect.left,
      right: rect.right,
      bottom: rect.bottom,
      width: rect.width,
      height: rect.height,
    },
  };
}

export function useMessageSelectionState(
  onSubmit: (selection: MessageSelectionSnapshot, comment: string) => void,
  disabled = false,
  annotationNumber = 1,
): MessageSelectionState {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [selection, setSelection] = useState<MessageSelectionSnapshot | null>(null);
  const [commentOpen, setCommentOpen] = useState(false);
  const [comment, setComment] = useState("");

  useEffect(() => {
    if (disabled) {
      setSelection(null);
      return;
    }

    const updateSelection = () => {
      if (commentOpen) return;
      const root = rootRef.current;
      if (!root) return;
      const next = selectionSnapshot(root);
      setSelection(next);
    };
    const clearOnEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setSelection(null);
      setCommentOpen(false);
    };

    document.addEventListener("selectionchange", updateSelection);
    document.addEventListener("keydown", clearOnEscape);
    return () => {
      document.removeEventListener("selectionchange", updateSelection);
      document.removeEventListener("keydown", clearOnEscape);
    };
  }, [commentOpen, disabled]);

  const openComment = useCallback(() => {
    if (!selection) return;
    setComment("");
    setCommentOpen(true);
  }, [selection]);

  const submit = useCallback(() => {
    if (!selection) return;
    onSubmit(selection, comment.trim());
    setComment("");
    setCommentOpen(false);
    setSelection(null);
    window.getSelection()?.removeAllRanges();
  }, [comment, onSubmit, selection]);

  const cancel = useCallback(() => {
    setComment("");
    setCommentOpen(false);
    setSelection(null);
  }, []);

  return { rootRef, selection, commentOpen, comment, setComment, openComment, submit, cancel, annotationNumber };
}

function popoverPosition(selection: MessageSelectionSnapshot, expanded: boolean): CSSProperties {
  const width = expanded ? 320 : 148;
  const left = Math.min(
    Math.max(12, selection.rect.left + selection.rect.width / 2 - width / 2),
    Math.max(12, window.innerWidth - width - 12),
  );
  const top = selection.rect.bottom + (expanded ? 10 : 8) + (expanded ? 0 : 0);
  const estimatedHeight = expanded ? 154 : 36;
  const adjustedTop = top + estimatedHeight > window.innerHeight - 12
    ? Math.max(12, selection.rect.top - estimatedHeight - 8)
    : top;
  return { left, top: adjustedTop };
}

export function MessageSelectionPopover({ state }: { state: MessageSelectionState }) {
  const { selection, commentOpen, comment, setComment, openComment, submit, cancel, annotationNumber } = state;
  const commentRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    if (commentOpen) commentRef.current?.focus();
  }, [commentOpen]);

  if (!selection) return null;

  return (
    <div
      role={commentOpen ? "dialog" : undefined}
      aria-label={commentOpen ? "Add a comment to the selected text" : "Selection actions"}
      className={`message-selection-popover${commentOpen ? " is-expanded" : ""}`}
      style={{
        ...popoverPosition(selection, commentOpen),
      }}
      onMouseDown={(event) => event.preventDefault()}
    >
      {!commentOpen ? (
        <button
          type="button"
          onClick={openComment}
          className="message-selection-popover__trigger"
        >
          <MessageSquarePlus size={14} strokeWidth={2} />
          添加批注
        </button>
      ) : (
        <>
          <div className="message-selection-popover__header">
            <span className="message-selection-popover__number">{annotationNumber}</span>
            <span>标记此处</span>
            <button
              type="button"
              aria-label="Cancel comment"
              onClick={cancel}
              className="message-selection-popover__close"
            >
              <X size={14} />
            </button>
          </div>
          <textarea
            ref={commentRef}
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                event.preventDefault();
                submit();
              }
              if (event.key === "Escape") {
                event.preventDefault();
                cancel();
              }
            }}
            placeholder="输入问题或修改意见…"
            rows={2}
            className="message-selection-popover__input"
          />
          <div className="message-selection-popover__footer">
            <button
              type="button"
              onClick={submit}
              className="message-selection-popover__submit"
            >
              <Check size={13} />
              添加
            </button>
          </div>
        </>
      )}
    </div>
  );
}
