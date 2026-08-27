import assert from "node:assert/strict";
import test from "node:test";
import { createJiti } from "jiti";

const jiti = createJiti(import.meta.url);
const { getDisplayToolPreview, resolveDisplayToolCall, unwrapProxyResultText } = await jiti.import("./tool-display.ts");

test("unwraps MCP proxy calls for labels, previews, and expanded input", () => {
  const call = resolveDisplayToolCall("mcp", {
    tool: "b3d_run_search_assets",
    args: { asset_type: "models", limit: 20, query: "ribbon" },
  });
  assert.equal(call.toolName, "b3d_run_search_assets");
  assert.deepEqual(call.input, { asset_type: "models", limit: 20, query: "ribbon" });
  assert.equal(getDisplayToolPreview(call.input), "ribbon");
});

test("never turns object-valued inputs into object Object previews", () => {
  assert.equal(getDisplayToolPreview({ filters: { type: "model" } }), "");
});

test("summarizes batch search queries instead of the result limit", () => {
  assert.equal(
    getDisplayToolPreview({
      queries: ["recent EEG diffusion model paper", "EEG motor imagery IEEE", "EEG sample augmentation"],
      numResults: 8,
    }),
    "recent EEG diffusion model paper · EEG motor imagery IEEE · +1",
  );
  assert.equal(getDisplayToolPreview({ numResults: 8 }), "");
});

test("removes the redundant result wrapper from MCP proxy results", () => {
  assert.equal(unwrapProxyResultText('{"result":[]}'), "[]");
  assert.equal(unwrapProxyResultText('{"result":{"count":2}}'), '{\n  "count": 2\n}');
  assert.equal(unwrapProxyResultText('{"data":[],"meta":{}}'), '{"data":[],"meta":{}}');
});
