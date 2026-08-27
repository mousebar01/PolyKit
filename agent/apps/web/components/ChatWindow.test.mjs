import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("./ChatWindow.tsx", import.meta.url), "utf8");
const messageViewSource = await readFile(new URL("./MessageView.tsx", import.meta.url), "utf8");

test("renders live assistant work with process detail styling", () => {
  assert.match(source, /keyPrefix: "live-process", processDetails: true/);
  assert.match(source, /isStreaming processDetails modelNames=/);
});

test("keeps copy actions out of process-detail fragments", () => {
  assert.match(messageViewSource, /textContent && !isStreaming && !processDetails/);
});

test("keeps the latest five activities in the scrollable live preview", () => {
  assert.match(source, /const MAX_LIVE_PREVIEW_ACTIVITIES = 5/);
  assert.match(source, /activities\.slice\(-limit\)/);
  assert.match(source, /livePreview=\{liveActivities\.map/);
  assert.match(source, /preview\.scrollTop = preview\.scrollHeight/);
});

test("completed work groups remain collapsed by default", () => {
  assert.match(source, /const \[expanded, setExpanded\] = useState\(isLive\)/);
  assert.match(source, /isLive && !expanded && livePreview/);
});

test("live work groups start expanded so active output is not preview-clipped", () => {
  assert.match(source, /const \[expanded, setExpanded\] = useState\(isLive\)/);
  assert.match(source, /\{expanded && \(/);
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
