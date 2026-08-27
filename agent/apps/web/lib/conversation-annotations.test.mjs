import assert from "node:assert/strict";
import test from "node:test";
import { createJiti } from "jiti";

const jiti = createJiti(import.meta.url, { tsconfigPaths: true });
const subject = await jiti.import("./conversation-annotations.ts");

test("serializes multiple annotations and restores their associations", async () => {
  const { parseAnnotatedMessage, serializeAnnotatedMessage } = subject;
  const annotations = [
    { id: "one", quote: "first line\nsecond line", comment: "Please verify this." },
    { id: "two", quote: "another passage", comment: "Can this be simpler?" },
  ];

  const serialized = serializeAnnotatedMessage("Keep the scope narrow.", annotations);
  const parsed = parseAnnotatedMessage(serialized);

  assert.equal(parsed.text, "Keep the scope narrow.");
  assert.deepEqual(parsed.annotations.map(({ quote, comment }) => ({ quote, comment })), [
    { quote: "first line\nsecond line", comment: "Please verify this." },
    { quote: "another passage", comment: "Can this be simpler?" },
  ]);
});

test("supports annotation-only prompts", async () => {
  const { parseAnnotatedMessage, serializeAnnotatedMessage } = subject;
  const parsed = parseAnnotatedMessage(serializeAnnotatedMessage("", [
    { id: "one", quote: "selected text", comment: "" },
  ]));

  assert.equal(parsed.text, "");
  assert.equal(parsed.annotations[0]?.quote, "selected text");
  assert.equal(parsed.annotations[0]?.comment, "");
});
