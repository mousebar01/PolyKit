import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("./ChatWindow.tsx", import.meta.url), "utf8");
const messageViewSource = await readFile(new URL("./MessageView.tsx", import.meta.url), "utf8");
const minimapSource = await readFile(new URL("./ChatMinimap.tsx", import.meta.url), "utf8");
const minimapCss = await readFile(new URL("./ChatMinimap.module.css", import.meta.url), "utf8");

test("renders live assistant work with process detail styling", () => {
  assert.match(source, /keyPrefix: "live-process", processDetails: true/);
  assert.match(source, /isStreaming processDetails modelNames=/);
});

test("keeps final-answer chrome out of process-detail fragments", () => {
  assert.match(messageViewSource, /!isStreaming && !processDetails/);
  assert.match(messageViewSource, /assistant-message-chrome/);
});

test("renders intermediate work directly without a group disclosure", () => {
  assert.match(source, /function ProcessTimeline\(/);
  assert.match(source, /<ProcessTimeline key=\{\`live-process-/);
  assert.match(source, /<ProcessTimeline>/);
  assert.doesNotMatch(source, /ProcessDetailsGroup/);
  assert.doesNotMatch(source, /process-details-toggle/);
  assert.doesNotMatch(source, /MAX_LIVE_PREVIEW_ACTIVITIES/);
  assert.doesNotMatch(source, /livePreview=/);
});

test("auto-collapses completed process work behind a thinking-duration summary", () => {
  assert.match(source, /function SettledProcessDisclosure\(/);
  assert.match(source, /const \[expanded, setExpanded\] = useState\(false\)/);
  assert.match(source, /hasFinalAssistantAnswer\(finalAssistant\)/);
  assert.match(source, /turnThinkingDuration\(anchorTimestamp, finalTimestamp\)/);
  assert.match(source, /chat\.thoughtSeconds/);
  assert.match(source, /chat\.thoughtMinutes/);
});

test("keeps thinking duration in minutes and seconds without an hour unit", () => {
  assert.match(source, /minutes: Math\.floor\(totalSeconds \/ 60\)/);
  assert.match(source, /seconds: totalSeconds % 60/);
  assert.doesNotMatch(source, /hours:/);
});

test("keeps a DSH-style turn status visible for the full agent run", () => {
  assert.match(source, /function AgentTurnStatus\(/);
  assert.match(source, /elapsedMs >= 15_000/);
  assert.match(source, /className="agent-turn-status"/);
  assert.match(source, /\{agentRunning && \(/);
  assert.match(source, /chat\.deepDiving/);
  assert.doesNotMatch(source, /phaseLabel\(/);
  assert.doesNotMatch(source, /animate-\[pulse_1\.5s_infinite\].*waitingModel/);
});

test("keeps the recent-turn rail compact and graduated", () => {
  assert.match(minimapSource, /const WINDOW_RADIUS = 6/);
  assert.match(minimapSource, /const HOVER_TICK_LENGTH = 24/);
  assert.match(minimapSource, /const BOOST_RADIUS = 3/);
  assert.match(minimapSource, /Math\.abs\(index - hoveredIndex\) \/ BOOST_RADIUS/);
  assert.doesNotMatch(minimapSource, /reply:/);
});

test("searches the current conversation by turn and reuses turn jumping", () => {
  assert.match(minimapSource, /function normalizeSearchText/);
  assert.match(minimapSource, /getFinalAnswerText/);
  assert.match(minimapSource, /turn\.searchText\.includes\(normalizedQuery\)/);
  assert.match(minimapSource, /const displayedTurns = useMemo/);
  assert.match(minimapSource, /: turns;/);
  assert.doesNotMatch(minimapSource, /source\.slice\(\)\.reverse\(\)/);
  assert.match(minimapSource, /data-current=\{turn\.index === activeIndex/);
  assert.match(minimapSource, /activateSearchResult[\s\S]*jumpTo\(turn\)/);
  assert.match(minimapSource, /onRevealHistory\(\)/);
  assert.match(minimapSource, /turn\.index \+ 1/);
  assert.match(minimapSource, /Search this conversation|chat\.searchConversationPlaceholder/);
});

test("supports keyboard search navigation and target highlighting", () => {
  assert.match(minimapSource, /event\.key === "ArrowDown"/);
  assert.match(minimapSource, /event\.key === "ArrowUp"/);
  assert.match(minimapSource, /event\.key === "Enter"/);
  assert.match(minimapSource, /event\.key === "Escape"/);
  assert.match(minimapSource, /styles\.searchTarget/);
  assert.match(minimapCss, /conversation-search-target/);
  assert.match(minimapCss, /\.turnNumber/);
  assert.match(minimapCss, /text-overflow:ellipsis/);
  assert.match(minimapCss, /white-space:nowrap/);
  assert.match(minimapCss, /prefers-reduced-motion/);
});

test("shows a bottom control for unread content and active runs", () => {
  assert.match(source, /const CHAT_BOTTOM_GAP = 24/);
  assert.match(source, /const SCROLL_BOTTOM_HIDE_DISTANCE = 16/);
  assert.match(source, /const SCROLL_BOTTOM_SHOW_DISTANCE = 32/);
  assert.match(source, /requestAnimationFrame\(performScrollToBottom\)/);
  assert.match(source, /\{isAwayFromBottom && \(/);
  assert.doesNotMatch(source, /reserveLiveTurnSpace|80vh/);
  assert.match(source, /scroll-running-dots/);
  assert.match(source, /<ArrowDown/);
});

test("sending a message re-enters bottom-following mode", () => {
  assert.match(source, /const handleSendAndFollow/);
  assert.match(source, /setAwayFromBottom\(false\);[\s\S]*handleSend\(message, images\);[\s\S]*requestAnimationFrame\(performScrollToBottom\)/);
  assert.match(source, /onSend=\{handleSendAndFollow\}/);
});

test("shows model identity only when the final-answer model changes", () => {
  assert.match(source, /hasFinalAssistantAnswer\(msg\)/);
  assert.match(source, /previousFinalAssistant\.provider !== assistant\.provider/);
  assert.match(source, /previousFinalAssistant\.model !== assistant\.model/);
  assert.match(source, /modelList\.find/);
  assert.match(source, /assistantIdentity=\{assistantIdentity\}/);
});

test("passes fork actions to rendered assistant messages", () => {
  assert.match(source, /onFork=\{sessionBusy \|\| isNew/);
  assert.match(source, /forking=\{forkingEntryId === entryIds\[idx\]\}/);
});

test("renders a turn-action shell when the final answer is empty", () => {
  assert.match(source, /const finalAnswerMessage = withAssistantBlocks\(finalAssistant, finalSplit\.answerBlocks\)/);
  assert.match(source, /rendered\.push\(renderMessage\(finalAssistantIdx, \{ messageOverride: finalAnswerMessage \}\)\)/);
});

test("keeps pending annotation highlights synchronized with the composer", () => {
  assert.match(source, /onAnnotationsChange=\{setPendingAnnotations\}/);
  assert.match(source, /pendingAnnotations=\{options\.processDetails \? undefined : pendingAnnotationsByEntryId\.get\(entryIds\[idx\]\)\}/);
  assert.match(source, /nextAnnotationNumber=\{pendingAnnotations\.length \+ 1\}/);
});
