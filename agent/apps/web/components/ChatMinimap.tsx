"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type RefObject,
} from "react";
import { splitFinalAssistantBlocks } from "@/lib/message-display";
import type {
  AgentMessage,
  AssistantMessage,
  TextContent,
  UserMessage,
} from "@/lib/types";
import styles from "./ChatMinimap.module.css";

interface Props {
  messages: AgentMessage[];
  streamingMessage: Partial<AgentMessage> | null;
  scrollContainer: RefObject<HTMLDivElement | null>;
  messageRefs: RefObject<(HTMLDivElement | null)[]>;
  onRevealHistory: () => void;
}

interface TurnInfo {
  userMessage: UserMessage;
  reply: string;
  scrollTop: number | null;
  index: number;
}

function createTurns(
  allMessages: Array<AgentMessage | Partial<AgentMessage>>,
): TurnInfo[] {
  const turns: TurnInfo[] = [];
  let currentTurn: TurnInfo | null = null;

  for (const message of allMessages) {
    if (message.role === "user") {
      currentTurn = {
        userMessage: message as UserMessage,
        reply: "",
        scrollTop: null,
        index: turns.length,
      };
      turns.push(currentTurn);
      continue;
    }
    if (message.role === "assistant" && currentTurn && !currentTurn.reply) {
      currentTurn.reply = getAssistantPreview(message);
    }
  }

  return turns;
}

const WINDOW_RADIUS = 15;
const TICK_LENGTH = 10;
const HOVER_TICK_LENGTH = 32;
const BOOST_RADIUS = 4;
const MAX_TICK_GAP = 14;
const TICK_HEIGHT = 14;
const ACTIVE_TICK_LENGTH = 20;
const ACTIVE_LOCK_MS = 1200;

function getMessageText(message: UserMessage): string {
  const raw = typeof message.content === "string"
    ? message.content
    : message.content
      .filter((block): block is TextContent => block.type === "text")
      .map((block) => block.text)
      .join(" ");
  return raw.replace(/\s+/g, " ").trim();
}

function getAssistantPreview(message: AgentMessage | Partial<AgentMessage>): string {
  if (message.role !== "assistant") return "";
  const { answerBlocks } = splitFinalAssistantBlocks(message as AssistantMessage);
  return answerBlocks
    .filter((block): block is TextContent => block.type === "text")
    .map((block) => block.text)
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
}

export function getMinimapWindow(activeIndex: number, total: number) {
  if (total <= 0) return null;
  const centerIndex = Math.max(0, Math.min(total - 1, activeIndex));
  return {
    startIndex: Math.max(0, centerIndex - WINDOW_RADIUS),
    endIndex: Math.min(total - 1, centerIndex + WINDOW_RADIUS),
    centerIndex,
  };
}

export function ChatMinimap({
  messages,
  streamingMessage,
  scrollContainer,
  messageRefs,
  onRevealHistory,
}: Props) {
  const allMessages = useMemo(
    () => (streamingMessage ? [...messages, streamingMessage] : messages),
    [messages, streamingMessage],
  );
  const [turns, setTurns] = useState<TurnInfo[]>(() => createTurns(allMessages));
  const [activeIndex, setActiveIndex] = useState(0);
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
  const [navHeight, setNavHeight] = useState(300);
  const navRef = useRef<HTMLElement>(null);
  const turnsRef = useRef<TurnInfo[]>([]);
  const measureTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const activeLockRef = useRef<{ index: number; until: number } | null>(null);
  const pendingIndexRef = useRef<number | null>(null);

  const allMessagesRef = useRef(allMessages);
  allMessagesRef.current = allMessages;
  turnsRef.current = turns;

  const syncActiveTurn = useCallback((scrollEl: HTMLDivElement, nextTurns = turnsRef.current) => {
    const lock = activeLockRef.current;
    if (lock && Date.now() < lock.until) {
      setActiveIndex(lock.index);
      return;
    }
    activeLockRef.current = null;

    const measured = nextTurns.filter((turn) => turn.scrollTop !== null);
    if (measured.length === 0) return;
    const viewportFocus = scrollEl.scrollTop + scrollEl.clientHeight * 0.3;
    const nearest = measured.reduce((best, turn) => (
      Math.abs((turn.scrollTop ?? 0) - viewportFocus)
        < Math.abs((best.scrollTop ?? 0) - viewportFocus)
        ? turn
        : best
    ), measured[0]);
    setActiveIndex(nearest.index);
  }, []);

  const measureTurns = useCallback(() => {
    if (measureTimerRef.current) return;
    measureTimerRef.current = setTimeout(() => {
      measureTimerRef.current = null;
      const scrollEl = scrollContainer.current;
      if (!scrollEl) return;

      const containerRect = scrollEl.getBoundingClientRect();
      const nextTurns: TurnInfo[] = [];
      let refIndex = 0;
      let currentTurn: TurnInfo | null = null;

      for (const message of allMessagesRef.current) {
        if (message.role !== "user" && message.role !== "assistant") continue;
        const element = messageRefs.current?.[refIndex] ?? null;
        refIndex += 1;

        if (message.role === "user") {
          const elementRect = element?.getBoundingClientRect();
          currentTurn = {
            userMessage: message as UserMessage,
            reply: "",
            scrollTop: elementRect
              ? elementRect.top - containerRect.top + scrollEl.scrollTop
              : null,
            index: nextTurns.length,
          };
          nextTurns.push(currentTurn);
          continue;
        }

        if (currentTurn && !currentTurn.reply) {
          currentTurn.reply = getAssistantPreview(message);
        }
      }

      turnsRef.current = nextTurns;
      setTurns(nextTurns);
      syncActiveTurn(scrollEl, nextTurns);

      const pendingIndex = pendingIndexRef.current;
      const pendingTurn = pendingIndex === null ? null : nextTurns[pendingIndex];
      if (pendingTurn?.scrollTop !== null && pendingTurn?.scrollTop !== undefined) {
        pendingIndexRef.current = null;
        activeLockRef.current = { index: pendingIndex!, until: Date.now() + ACTIVE_LOCK_MS };
        setActiveIndex(pendingIndex!);
        scrollEl.scrollTo({
          top: Math.max(0, pendingTurn.scrollTop - scrollEl.clientHeight * 0.3),
          behavior: "smooth",
        });
      }
    }, 100);
  }, [messageRefs, scrollContainer, syncActiveTurn]);

  useEffect(() => {
    const scrollEl = scrollContainer.current;
    if (!scrollEl) return;
    const handleScroll = () => syncActiveTurn(scrollEl);
    scrollEl.addEventListener("scroll", handleScroll, { passive: true });
    return () => scrollEl.removeEventListener("scroll", handleScroll);
  }, [scrollContainer, syncActiveTurn]);

  useEffect(() => {
    const scrollEl = scrollContainer.current;
    if (!scrollEl) return;
    const resizeObserver = new ResizeObserver(() => {
      measureTurns();
    });
    resizeObserver.observe(scrollEl);
    if (scrollEl.firstElementChild) resizeObserver.observe(scrollEl.firstElementChild);
    measureTurns();
    return () => resizeObserver.disconnect();
  }, [measureTurns, scrollContainer]);

  useEffect(() => {
    const timeout = setTimeout(measureTurns, 50);
    return () => clearTimeout(timeout);
  }, [allMessages.length, measureTurns]);

  useEffect(() => {
    const nav = navRef.current;
    if (!nav) return;
    const updateHeight = () => setNavHeight(nav.getBoundingClientRect().height || 300);
    updateHeight();
    const resizeObserver = new ResizeObserver(updateHeight);
    resizeObserver.observe(nav);
    return () => resizeObserver.disconnect();
  }, [turns.length]);

  useEffect(() => () => {
    if (measureTimerRef.current) {
      clearTimeout(measureTimerRef.current);
      measureTimerRef.current = null;
    }
  }, []);

  const jumpTo = useCallback((turn: TurnInfo) => {
    const scrollEl = scrollContainer.current;
    if (!scrollEl) return;
    activeLockRef.current = { index: turn.index, until: Date.now() + ACTIVE_LOCK_MS };
    setActiveIndex(turn.index);
    if (turn.scrollTop === null) {
      pendingIndexRef.current = turn.index;
      onRevealHistory();
      setTimeout(measureTurns, 50);
      return;
    }
    scrollEl.scrollTo({
      top: Math.max(0, turn.scrollTop - scrollEl.clientHeight * 0.3),
      behavior: "smooth",
    });
  }, [measureTurns, onRevealHistory, scrollContainer]);

  if (turns.length === 0) return null;

  const windowState = getMinimapWindow(activeIndex, turns.length);
  if (!windowState) return null;
  const windowTurns = turns.slice(windowState.startIndex, windowState.endIndex + 1);
  const gap = windowTurns.length > 1
    ? Math.max(0, Math.min(MAX_TICK_GAP, (navHeight - windowTurns.length * TICK_HEIGHT) / (windowTurns.length - 1)))
    : 0;
  const slot = TICK_HEIGHT + gap;

  return (
    <nav
      ref={navRef}
      className={styles.navigator}
      aria-label="Message navigation"
      onMouseLeave={() => setHoveredIndex(null)}
    >
      {windowTurns.map((turn, offset) => {
        const index = windowState.startIndex + offset;
        const isActive = index === windowState.centerIndex;
        const isHovered = index === hoveredIndex;
        const boost = hoveredIndex === null
          ? 0
          : Math.max(0, 1 - Math.abs(index - hoveredIndex) / BOOST_RADIUS);
        const length = Math.round(TICK_LENGTH + (HOVER_TICK_LENGTH - TICK_LENGTH) * boost);
        const tickWidth = isActive ? Math.max(ACTIVE_TICK_LENGTH, length) : length;
        const tickBackground = isActive
          ? (isHovered ? "var(--accent-hover)" : "var(--accent)")
          : `color-mix(in srgb, var(--text) ${35 + Math.round(boost * 65)}%, var(--text-muted))`;
        const prompt = getMessageText(turn.userMessage) || "Empty message";

        return (
          <button
            key={index}
            type="button"
            className={styles.tickButton}
            style={{
              transform: `translateY(calc(-50% + ${(offset - windowTurns.length / 2) * slot + TICK_HEIGHT / 2}px))`,
            }}
            aria-label={`Jump to: ${prompt}`}
            aria-current={isActive ? "true" : undefined}
            onClick={() => jumpTo(turn)}
            onMouseEnter={() => setHoveredIndex(index)}
            onFocus={() => setHoveredIndex(index)}
            onBlur={() => setHoveredIndex(null)}
          >
            <span
              className={styles.tick}
              aria-hidden="true"
              data-active={isActive ? "true" : undefined}
              style={{
                width: tickWidth,
                height: isHovered ? 4 : 2,
                background: tickBackground,
              }}
            />
            <span className={styles.preview} aria-hidden="true" data-open={isHovered ? "true" : undefined}>
              <span className={styles.prompt}>{prompt}</span>
              {turn.reply && <span className={styles.reply}>{turn.reply}</span>}
            </span>
          </button>
        );
      })}
    </nav>
  );
}

export function useMessageRefs(count: number): RefObject<(HTMLDivElement | null)[]> {
  const refs = useRef<(HTMLDivElement | null)[]>([]);
  refs.current = Array(count).fill(null).map((_, index) => refs.current[index] ?? null);
  return refs;
}
