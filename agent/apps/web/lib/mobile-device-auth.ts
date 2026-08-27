import { createHash, randomBytes, randomUUID, timingSafeEqual } from "node:crypto";
import { chmodSync, existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import lockfile from "proper-lockfile";

const MOBILE_TOKEN_PREFIX = "pim_";
const AUTH_FILE_OPTIONS = { encoding: "utf8" as const, mode: 0o600 };

export interface MobileDevice {
  id: string;
  name: string;
  tokenHash: string;
  createdAt: string;
}

interface MobileDeviceFile {
  version: 1;
  devices: MobileDevice[];
}

export function getMobileDevicesPath(): string {
  return process.env.PI_MOBILE_DEVICES_PATH || join(homedir(), ".pi", "agent", "mobile-devices.json");
}

function tokenHash(token: string): string {
  return createHash("sha256").update(token, "utf8").digest("hex");
}

function equalHash(left: string, right: string): boolean {
  const leftBuffer = Buffer.from(left, "hex");
  const rightBuffer = Buffer.from(right, "hex");
  return leftBuffer.length === rightBuffer.length && timingSafeEqual(leftBuffer, rightBuffer);
}

function bearerTokenHash(authorization: string | null): string | null {
  const match = /^Bearer\s+(pim_[A-Za-z0-9_-]+)$/i.exec(authorization ?? "");
  return match ? tokenHash(match[1]) : null;
}

function readDevices(path: string): MobileDeviceFile {
  if (!existsSync(path)) return { version: 1, devices: [] };
  try {
    const value = JSON.parse(readFileSync(path, "utf8")) as Partial<MobileDeviceFile>;
    if (value.version !== 1 || !Array.isArray(value.devices)) return { version: 1, devices: [] };
    return { version: 1, devices: value.devices.filter((device): device is MobileDevice => (
      Boolean(device)
      && typeof device.id === "string"
      && typeof device.name === "string"
      && typeof device.tokenHash === "string"
      && typeof device.createdAt === "string"
    )) };
  } catch {
    return { version: 1, devices: [] };
  }
}

function ensureFile(path: string): void {
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
  if (!existsSync(path)) writeFileSync(path, JSON.stringify({ version: 1, devices: [] }, null, 2), AUTH_FILE_OPTIONS);
  chmodSync(path, 0o600);
}

async function updateDevices<T>(path: string, update: (file: MobileDeviceFile) => T): Promise<T> {
  ensureFile(path);
  const release = await lockfile.lock(path, { retries: { retries: 8, minTimeout: 50, maxTimeout: 1000 }, stale: 30_000 });
  try {
    const file = readDevices(path);
    const result = update(file);
    writeFileSync(path, JSON.stringify(file, null, 2), AUTH_FILE_OPTIONS);
    chmodSync(path, 0o600);
    return result;
  } finally {
    await release().catch(() => {});
  }
}

export function isValidMobileBearerAuthorization(authorization: string | null, path = getMobileDevicesPath()): boolean {
  const suppliedHash = bearerTokenHash(authorization);
  if (!suppliedHash) return false;
  return readDevices(path).devices.some((device) => equalHash(device.tokenHash, suppliedHash));
}

export function getMobileDeviceForAuthorization(
  authorization: string | null,
  path = getMobileDevicesPath(),
): Omit<MobileDevice, "tokenHash"> | null {
  const suppliedHash = bearerTokenHash(authorization);
  if (!suppliedHash) return null;
  const device = readDevices(path).devices.find((candidate) => equalHash(candidate.tokenHash, suppliedHash));
  if (!device) return null;
  return { id: device.id, name: device.name, createdAt: device.createdAt };
}

export async function revokeMobileDeviceForAuthorization(
  authorization: string | null,
  path = getMobileDevicesPath(),
): Promise<boolean> {
  const suppliedHash = bearerTokenHash(authorization);
  if (!suppliedHash) return false;
  return updateDevices(path, (file) => {
    const index = file.devices.findIndex((device) => equalHash(device.tokenHash, suppliedHash));
    if (index === -1) return false;
    file.devices.splice(index, 1);
    return true;
  });
}

export async function createMobileDevice(name: string, path = getMobileDevicesPath()): Promise<{ device: Omit<MobileDevice, "tokenHash">; token: string }> {
  const token = `${MOBILE_TOKEN_PREFIX}${randomBytes(32).toString("base64url")}`;
  const device: MobileDevice = {
    id: randomUUID(),
    name: name.trim().slice(0, 80) || "Mobile device",
    tokenHash: tokenHash(token),
    createdAt: new Date().toISOString(),
  };
  await updateDevices(path, (file) => { file.devices.push(device); });
  return { device: { id: device.id, name: device.name, createdAt: device.createdAt }, token };
}

export function listMobileDevices(path = getMobileDevicesPath()): Array<Omit<MobileDevice, "tokenHash">> {
  return readDevices(path).devices.map((device) => ({
    id: device.id,
    name: device.name,
    createdAt: device.createdAt,
  }));
}

export async function revokeMobileDevice(id: string, path = getMobileDevicesPath()): Promise<boolean> {
  return updateDevices(path, (file) => {
    const index = file.devices.findIndex((device) => device.id === id);
    if (index === -1) return false;
    file.devices.splice(index, 1);
    return true;
  });
}

export async function renameMobileDevice(id: string, name: string, path = getMobileDevicesPath()): Promise<Omit<MobileDevice, "tokenHash"> | null> {
  return updateDevices(path, (file) => {
    const device = file.devices.find((candidate) => candidate.id === id);
    if (!device) return null;
    device.name = name.trim().slice(0, 80) || device.name;
    return { id: device.id, name: device.name, createdAt: device.createdAt };
  });
}
