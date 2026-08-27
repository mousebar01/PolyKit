"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type RefObject,
} from "react";
import { Search, X } from "lucide-react";
import { useI18n } from "@/hooks/useI18n";
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
  prompt: string;
  answer: string;
  searchText: string;
  scrollTop: number | null;
  element: HTMLDivElement | null;
  index: number;
}

function normalizeSearchText(value: string): string {
  return value.replace(/\s+/g, " ").trim().toLocaleLowerCase();
}

function getFinalAnswerText(message: AgentMessage | Partial<AgentMessage>): string {
  if (message.role !== "assistant") return "";
  const { answerBlocks } = splitFinalAssistantBlocks(message as AssistantMessage);
  return answerBlocks
    .filter((block): block is TextContent => block.type === "text")
    .map((block) => block.text)
    .join(" ")
    .replace(/\s+/g, " ")
    .trim();
}

function createTurns(
  allMessages: Array<AgentMessage | Partial<AgentMessage>>,
): TurnInfo[] {
  const turns: TurnInfo[] = [];
  let currentTurn: TurnInfo | null = null;

  for (const message of allMessages) {
    if (message.role === "user") {
      const userMessage = message as UserMessage;
      const prompt = getMessageText(userMessage);
      currentTurn = {
        userMessage,
        prompt,
        answer: "",
        searchText: normalizeSearchText(prompt),
        scrollTop: null,
        element: null,
        index: turns.length,
      };
      turns.push(currentTurn);
      continue;
    }

    if (message.role === "assistant" && currentTurn) {
      const answer = getFinalAnswerText(message);
      if (!answer) continue;
      currentTurn.answer = [currentTurn.answer, answer].filter(Boolean).join(" ");
      currentTurn.searchText = normalizeSearchText(`${currentTurn.prompt} ${currentTurn.answer}`);
    }
  }

  return turns;
}

const WINDOW_RADIUS = 6;
const TICK_LENGTH = 8;
const HOVER_TICK_LENGTH = 24;
const BOOST_RADIUS = 3;
const MAX_TICK_GAP = 10;
const TICK_HEIGHT = 16;
const ACTIVE_TICK_LENGTH = 18;
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
  const { t } = useI18n();
  const [hoveredIndex, setHoveredIndex] = useState<number | null>(null);
  const [searchOpen, setSearchOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [selectedResult, setSelectedResult] = useState(0);
  const [navHeight, setNavHeight] = useState(300);
  const navRef = useRef<HTMLElement>(null);
  const searchInputRef = useRef<HTMLInputElement>(null);
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
      const indexedTurns = createTurns(allMessagesRef.current);
      const nextTurns: TurnInfo[] = [];
      let refIndex = 0;

      for (const message of allMessagesRef.current) {
        if (message.role !== "user" && message.role !== "assistant") continue;
        const element = messageRefs.current?.[refIndex] ?? null;
        refIndex += 1;

        if (message.role === "user") {
          const elementRect = element?.getBoundingClientRect();
          const indexedTurn = indexedTurns[nextTurns.length];
          const currentTurn: TurnInfo = {
            ...indexedTurn,
            userMessage: message as UserMessage,
            scrollTop: elementRect
              ? elementRect.top - containerRect.top + scrollEl.scrollTop
              : null,
            element,
            index: nextTurns.length,
          };
          nextTurns.push(currentTurn);
          continue;
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
        window.setTimeout(() => {
          pendingTurn.element?.classList.add(styles.searchTarget);
          window.setTimeout(() => pendingTurn.element?.classList.remove(styles.searchTarget), 900);
        }, 280);
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

  const flashTurn = useCallback((turn: TurnInfo) => {
    if (!turn.element) return;
    turn.element.classList.remove(styles.searchTarget);
    void turn.element.offsetWidth;
    turn.element.classList.add(styles.searchTarget);
    window.setTimeout(() => turn.element?.classList.remove(styles.searchTarget), 900);
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
    window.setTimeout(() => flashTurn(turn), 280);
  }, [flashTurn, measureTurns, onRevealHistory, scrollContainer]);

  const normalizedQuery = normalizeSearchText(query);
  const searchResults = useMemo(() => {
    if (!normalizedQuery) return [];
    return turns
      .filter((turn) => turn.searchText.includes(normalizedQuery))
      .slice()
      .reverse()
      .slice(0, 20);
  }, [normalizedQuery, turns]);

  useEffect(() => {
    if (!searchOpen) return;
    setSelectedResult(0);
    requestAnimationFrame(() => searchInputRef.current?.focus());
  }, [searchOpen]);

  useEffect(() => {
    setSelectedResult(0);
  }, [normalizedQuery]);

  const closeSearch = useCallback(() => {
    setSearchOpen(false);
    setQuery("");
    setSelectedResult(0);
  }, []);

  const activateSearchResult = useCallback((turn: TurnInfo) => {
    closeSearch();
    jumpTo(turn);
  }, [closeSearch, jumpTo]);

  const handleSearchKeyDown = useCallback((event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeSearch();
      return;
    }
    if (searchResults.length === 0) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setSelectedResult((current) => (current + 1) % searchResults.length);
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setSelectedResult((current) => (current - 1 + searchResults.length) % searchResults.length);
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      activateSearchResult(searchResults[selectedResult] ?? searchResults[0]);
    }
  }, [activateSearchResult, closeSearch, searchResults, selectedResult]);

  const resultContext = useCallback((turn: TurnInfo) => {
    if (!normalizedQuery) return "";
    const promptMatch = normalizeSearchText(turn.prompt).includes(normalizedQuery);
    return promptMatch ? turn.prompt : turn.answer;
  }, [normalizedQuery]);

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
      aria-label={t("chat.turnNavigation")}
      onMouseLeave={() => setHoveredIndex(null)}
    >
      <div className={styles.tickRail}>
      {windowTurns.map((turn, offset) => {
        const index = windowState.startIndex + offset;
        const isActive = index === windowState.centerIndex;
        const isHovered = index === hoveredIndex;
        const boost = hoveredIndex === null
          ? 0
          : Math.max(0, 1 - Math.abs(index - hoveredIndex) / BOOST_RADIUS);
        const hoverWidth = Math.round(TICK_LENGTH + (HOVER_TICK_LENGTH - TICK_LENGTH) * boost);
        const tickWidth = isActive ? Math.max(ACTIVE_TICK_LENGTH, hoverWidth) : hoverWidth;
        const tickBackground = isActive
          ? "var(--text)"
          : boost > 0
            ? `color-mix(in srgb, var(--text-muted) ${45 + Math.round(boost * 55)}%, var(--text-dim))`
            : "var(--text-dim)";
        const prompt = turn.prompt || t("chat.emptyMessage");

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
                height: isActive || isHovered ? 2 : 1,
                background: tickBackground,
              }}
            />
            <span className={styles.preview} aria-hidden="true" data-open={isHovered ? "true" : undefined}>
              <span className={styles.prompt}>{prompt}</span>
            </span>
          </button>
        );
      })}
      </div>

      <button
        type="button"
        className={styles.searchButton}
        aria-label={t("chat.searchConversation")}
        title={t("chat.searchConversation")}
        aria-expanded={searchOpen}
        onClick={() => setSearchOpen((open) => !open)}
      >
        <Search size={14} strokeWidth={1.8} aria-hidden="true" />
      </button>

      {searchOpen && (
        <div className={styles.searchPanel}>
          <div className={styles.searchHeader}>
            <Search size={14} strokeWidth={1.8} aria-hidden="true" />
            <input
              ref={searchInputRef}
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={handleSearchKeyDown}
              placeholder={t("chat.searchConversationPlaceholder")}
              aria-label={t("chat.searchConversation")}
            />
            <button type="button" onClick={closeSearch} aria-label={t("chat.close")} className={styles.searchClose}>
              <X size={14} strokeWidth={1.8} aria-hidden="true" />
            </button>
          </div>

          <div className={styles.searchResults} role="listbox" aria-label={t("chat.searchResults")}>
            {!normalizedQuery ? (
              <div className={styles.searchEmpty}>{t("chat.searchConversationHint")}</div>
            ) : searchResults.length === 0 ? (
              <div className={styles.searchEmpty}>{t("i18n.noResults")}</div>
            ) : (
              searchResults.map((turn, resultIndex) => (
                <button
                  key={turn.index}
                  type="button"
                  role="option"
                  aria-selected={resultIndex === selectedResult}
                  className={styles.searchResult}
                  data-selected={resultIndex === selectedResult || undefined}
                  onMouseEnter={() => setSelectedResult(resultIndex)}
                  onClick={() => activateSearchResult(turn)}
                >
                  <span className={styles.searchResultPrompt}>{turn.prompt || t("chat.emptyMessage")}</span>
                  <span className={styles.searchResultContext}>{resultContext(turn)}</span>
                </button>
              ))
            )}
          </div>
        </div>
      )}
    </nav>
  );
}

export function useMessageRefs(count: number): RefObject<(HTMLDivElement | null)[]> {
  const refs = useRef<(HTMLDivElement | null)[]>([]);
  refs.current = Array(count).fill(null).map((_, index) => refs.current[index] ?? null);
  return refs;
}
