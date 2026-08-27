import assert from "node:assert/strict";
import { mkdtemp, readFile, stat } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createJiti } from "jiti";
import test from "node:test";

const jiti = createJiti(import.meta.url);
async function subject() { return jiti.import("./mobile-device-auth.ts"); }

test("issues hashed mobile tokens and validates only the matching bearer token", async () => {
  const path = join(await mkdtemp(join(tmpdir(), "polykit-agent-mobile-auth-")), "devices.json");
  const { createMobileDevice, isValidMobileBearerAuthorization } = await subject();
  const { token } = await createMobileDevice("Pixel", path);
  const contents = await readFile(path, "utf8");
  assert.equal(contents.includes(token), false);
  assert.equal(isValidMobileBearerAuthorization(`Bearer ${token}`, path), true);
  assert.equal(isValidMobileBearerAuthorization("Bearer pim_wrong", path), false);
  assert.equal(isValidMobileBearerAuthorization(null, path), false);
  // Windows does not expose POSIX permission bits; 0o600 is enforced by
  // chmodSync on POSIX systems only (Windows uses ACLs instead).
  if (process.platform !== "win32") {
    assert.equal((await stat(path)).mode & 0o777, 0o600);
  }
});

test("lists public device metadata and revokes one device independently", async () => {
  const path = join(await mkdtemp(join(tmpdir(), "polykit-agent-mobile-auth-")), "devices.json");
  const { createMobileDevice, isValidMobileBearerAuthorization, listMobileDevices, revokeMobileDevice } = await subject();
  const first = await createMobileDevice("Phone", path);
  const second = await createMobileDevice("Tablet", path);
  assert.deepEqual(listMobileDevices(path).map((device) => device.name), ["Phone", "Tablet"]);
  assert.equal(Object.hasOwn(listMobileDevices(path)[0], "tokenHash"), false);
  assert.equal(await revokeMobileDevice(first.device.id, path), true);
  assert.equal(isValidMobileBearerAuthorization(`Bearer ${first.token}`, path), false);
  assert.equal(isValidMobileBearerAuthorization(`Bearer ${second.token}`, path), true);
  assert.equal(await revokeMobileDevice("missing", path), false);
});

test("looks up and revokes only the device represented by a bearer token", async () => {
  const path = join(await mkdtemp(join(tmpdir(), "polykit-agent-mobile-auth-")), "devices.json");
  const {
    createMobileDevice,
    getMobileDeviceForAuthorization,
    isValidMobileBearerAuthorization,
    revokeMobileDeviceForAuthorization,
  } = await subject();
  const first = await createMobileDevice("My phone", path);
  const second = await createMobileDevice("Other phone", path);
  assert.deepEqual(
    getMobileDeviceForAuthorization(`Bearer ${first.token}`, path),
    first.device,
  );
  assert.equal(getMobileDeviceForAuthorization("Basic nope", path), null);
  assert.equal(await revokeMobileDeviceForAuthorization(`Bearer ${first.token}`, path), true);
  assert.equal(isValidMobileBearerAuthorization(`Bearer ${first.token}`, path), false);
  assert.equal(isValidMobileBearerAuthorization(`Bearer ${second.token}`, path), true);
  assert.equal(await revokeMobileDeviceForAuthorization(`Bearer ${first.token}`, path), false);
});
