import assert from "node:assert/strict";
import test from "node:test";
import { createJiti } from "jiti";

const jiti = createJiti(import.meta.url);

function authorization(username, password) {
  return `Basic ${Buffer.from(`${username}:${password}`, "utf8").toString("base64")}`;
}

async function loadSubject() {
  return jiti.import("./web-auth.ts");
}

test("enables password authentication only for a non-empty configured password", async () => {
  const { isWebPasswordEnabled } = await loadSubject();
  assert.equal(isWebPasswordEnabled(undefined), false);
  assert.equal(isWebPasswordEnabled(""), false);
  assert.equal(isWebPasswordEnabled("secret"), true);
});

test("validates a configurable username and password", async () => {
  const { isValidBasicAuthorization } = await loadSubject();
  assert.equal(isValidBasicAuthorization(authorization("operator", "secret"), "secret", "operator"), true);
  assert.equal(isValidBasicAuthorization(authorization("pi", "secret"), "secret", "operator"), false);
  assert.equal(isValidBasicAuthorization(authorization("operator", "wrong"), "secret", "operator"), false);
});

test("supports UTF-8 passwords and colons in the password", async () => {
  const { isValidBasicAuthorization } = await loadSubject();
  const password = "口令:with:colons";
  assert.equal(isValidBasicAuthorization(authorization("operator", password), password, "operator"), true);
});

test("rejects missing, malformed, and non-canonical authorization values", async () => {
  const { isValidBasicAuthorization } = await loadSubject();
  const valid = authorization("operator", "secret");

  assert.equal(isValidBasicAuthorization(null, "secret", "operator"), false);
  assert.equal(isValidBasicAuthorization("Bearer token", "secret", "operator"), false);
  assert.equal(isValidBasicAuthorization("Basic !!!", "secret", "operator"), false);
  assert.equal(isValidBasicAuthorization(`${valid}!`, "secret", "operator"), false);
  assert.equal(isValidBasicAuthorization(
    `Basic ${Buffer.from("missing-separator", "utf8").toString("base64")}`,
    "secret",
    "operator",
  ), false);
});

test("does not authenticate when password protection is disabled", async () => {
  const { isValidBasicAuthorization } = await loadSubject();
  assert.equal(isValidBasicAuthorization(authorization("operator", ""), "", "operator"), false);
  assert.equal(isValidBasicAuthorization(authorization("operator", "secret"), undefined), false);
});
