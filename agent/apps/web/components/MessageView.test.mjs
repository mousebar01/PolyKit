import assert from "node:assert/strict";
import test from "node:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createJiti } from "jiti";

const jiti = createJiti(import.meta.url, {
  jsx: { runtime: "automatic" },
  tsconfigPaths: true,
});
const { MessageView, summarizeThinkingPreview, toolActionKind, toolActionLabel } = await jiti.import("./MessageView.tsx");
const { I18nProvider } = await jiti.import("../hooks/useI18n.tsx");

function renderMessage(message, props = {}) {
  return renderToStaticMarkup(
    React.createElement(
      I18nProvider,
      null,
      React.createElement(MessageView, { message, ...props }),
    ),
  );
}

test("renders a provider error when the assistant message has no content", () => {
  const html = renderMessage({
    role: "assistant",
    provider: "openai",
    model: "gpt-test",
    content: [],
    stopReason: "error",
    errorMessage: "OpenAI API error (403): <html>request forbidden</html>",
  });

  assert.match(html, /role="alert"/);
  assert.match(html, /Error: OpenAI API error \(403\)/);
  assert.match(html, /&lt;html&gt;request forbidden&lt;\/html&gt;/);
});

test("renders partial assistant content before the provider error", () => {
  const html = renderMessage({
    role: "assistant",
    provider: "openai",
    model: "gpt-test",
    content: [{ type: "text", text: "Partial response" }],
    stopReason: "error",
    errorMessage: "Connection closed",
  });

  assert.match(html, /Partial response/);
  assert.match(html, /Error: Connection closed/);
});

test("maps implementation tool names to semantic actions", () => {
  const translate = (key) => key;
  assert.equal(toolActionLabel("Read", translate), "chat.action.read");
  assert.equal(toolActionLabel("apply_patch", translate), "chat.action.edit");
  assert.equal(toolActionLabel("WebSearch", translate), "chat.action.search");
  assert.equal(toolActionLabel("exec_command", translate), "chat.action.run");
  assert.equal(toolActionLabel("custom_tool", translate), "chat.action.useTool");
  assert.equal(toolActionKind("Read"), "read");
  assert.equal(toolActionKind("apply_patch"), "edit");
  assert.equal(toolActionKind("WebSearch"), "search");
  assert.equal(toolActionKind("exec_command"), "run");
});

test("renders concrete batch search queries in the process row", () => {
  const html = renderMessage({
    role: "assistant",
    content: [{
      type: "toolCall",
      toolCallId: "search-1",
      toolName: "web_search",
      input: {
        queries: ["recent EEG diffusion model paper", "EEG motor imagery IEEE", "EEG sample augmentation"],
        numResults: 8,
      },
    }],
  }, {
    toolResults: new Map([["search-1", {
      role: "toolResult",
      toolCallId: "search-1",
      toolName: "web_search",
      content: [{ type: "text", text: "Completed queries: 3" }],
      isError: false,
    }]]),
  });

  assert.match(html, /recent EEG diffusion model paper · EEG motor imagery IEEE · \+1/);
  assert.doesNotMatch(html, />8</);
  assert.match(html, /action-search is-success/);
  assert.match(html, /class="lucide lucide-search tool-call-icon"/);
});

test("uses DSH-style first-line and live-tail thinking summaries", () => {
  assert.equal(summarizeThinkingPreview("先搜索相关项目。然后比较实现细节。\n开始读取文件"), "先搜索相关项目。然后比较实现细节。");
  assert.equal(summarizeThinkingPreview("Inspect repository\nCompare the implementations", 160, true), "Compare the implementations");
  assert.equal(summarizeThinkingPreview("  Compare   the available approaches  "), "Compare the available approaches");
});

test("renders only the active streaming block as a live transcript row", () => {
  const html = renderMessage({
    role: "assistant",
    provider: "openai",
    model: "gpt-test",
    content: [
      { type: "thinking", thinking: "Inspect repository\nRead message renderer" },
      { type: "thinking", thinking: "Compare styles\nApply DSH transcript layout" },
    ],
  }, { isStreaming: true });

  assert.match(html, /data-state="settled"/);
  assert.match(html, /data-state="running"/);
  assert.match(html, /Apply DSH transcript layout/);
  assert.doesNotMatch(html, /gpt-test/);
  assert.doesNotMatch(html, /t\/s/);
});

test("renders quiet final-answer chrome with optional model identity", () => {
  const html = renderMessage({
    role: "assistant",
    provider: "openai",
    model: "gpt-test",
    content: [{ type: "text", text: "Completed answer" }],
  }, {
    entryId: "answer-entry",
    onFork: () => {},
    assistantIdentity: "GPT Test",
  });

  assert.match(html, /assistant-message-chrome/);
  assert.match(html, /assistant-model-identity/);
  assert.match(html, />GPT Test</);
  assert.match(html, /aria-label="复制消息"/);
  assert.match(html, /aria-label="另起对话"/);
  assert.match(html, /lucide-copy/);
  assert.match(html, /lucide-git-branch/);
});

test("keeps forking available when a completed turn has no final answer", () => {
  const html = renderMessage({
    role: "assistant",
    provider: "openai",
    model: "gpt-test",
    content: [],
  }, { entryId: "process-only-entry", onFork: () => {} });

  assert.match(html, /aria-label="另起对话"/);
  assert.match(html, /assistant-message-actions/);
  assert.match(html, /assistant-message-action/);
});
