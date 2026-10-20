import { spawn } from "node:child_process";
import { rm } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(SCRIPT_DIR, "..");
const DATA_DIR = path.resolve(ROOT, "data");
const TEST_ROOT = path.resolve(DATA_DIR, "sync-test-profiles");
const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const BOOKBOT_URL = "http://127.0.0.1:4173/?sync-test=1&v=33";
const NOTE_ID = "__bookbot_incremental_sync_test__";

if (!TEST_ROOT.startsWith(`${DATA_DIR}${path.sep}`)) throw new Error("Unsafe sync test directory");

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

class CdpClient {
  constructor(url) {
    this.nextId = 1;
    this.pending = new Map();
    this.socket = new WebSocket(url);
    this.ready = new Promise((resolve, reject) => {
      this.socket.addEventListener("open", resolve, { once: true });
      this.socket.addEventListener("error", reject, { once: true });
    });
    this.socket.addEventListener("message", (event) => {
      const message = JSON.parse(event.data);
      if (!message.id || !this.pending.has(message.id)) return;
      const { resolve, reject } = this.pending.get(message.id);
      this.pending.delete(message.id);
      if (message.error) reject(new Error(message.error.message)); else resolve(message.result);
    });
  }

  async send(method, params = {}) {
    await this.ready;
    const id = this.nextId++;
    const result = new Promise((resolve, reject) => this.pending.set(id, { resolve, reject }));
    this.socket.send(JSON.stringify({ id, method, params }));
    return result;
  }

  async evaluate(expression) {
    const result = await this.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || "Browser evaluation failed");
    return result.result?.value;
  }

  close() {
    this.socket.close();
  }
}

async function endpoint(port, route) {
  const response = await fetch(`http://127.0.0.1:${port}${route}`);
  if (!response.ok) throw new Error(`CDP ${port}${route} returned ${response.status}`);
  return response.json();
}

async function waitForEndpoint(port) {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try {
      const pages = await endpoint(port, "/json/list");
      const page = pages.find((item) => item.type === "page" && item.url.startsWith("http://127.0.0.1:4173/"));
      if (page?.webSocketDebuggerUrl) return page.webSocketDebuggerUrl;
    } catch {}
    await delay(150);
  }
  throw new Error(`Edge test device on port ${port} did not become ready`);
}

async function launchDevice(name, port) {
  const profile = path.join(TEST_ROOT, name);
  const process = spawn(EDGE, [
    "--headless=new",
    "--disable-gpu",
    "--no-first-run",
    `--remote-debugging-port=${port}`,
    "--remote-debugging-address=127.0.0.1",
    `--user-data-dir=${profile}`,
    BOOKBOT_URL,
  ], { stdio: "ignore", windowsHide: true });
  const client = new CdpClient(await waitForEndpoint(port));
  for (let attempt = 0; attempt < 80; attempt += 1) {
    const ready = await client.evaluate("typeof saveServerProfile === 'function' && profileSyncReady && !profileSyncBusy");
    if (ready) return { name, port, process, client };
    await delay(150);
  }
  throw new Error(`${name} loaded BookBot but did not finish initial sync`);
}

async function closeDevice(device) {
  if (!device) return;
  try { await device.client.send("Browser.close"); } catch {}
  device.client.close();
  await delay(300);
  if (!device.process.killed) device.process.kill();
}

await rm(TEST_ROOT, { recursive: true, force: true });
let deviceA;
let deviceB;
try {
  deviceA = await launchDevice("device-a", 9324);
  deviceB = await launchDevice("device-b", 9325);
  const deviceIds = await Promise.all([deviceA, deviceB].map((device) => device.client.evaluate("localStorage.getItem('bookbot-device-id-v1')")));
  if (!deviceIds[0] || !deviceIds[1] || deviceIds[0] === deviceIds[1]) throw new Error("Test browsers did not receive independent device IDs");

  const created = await deviceA.client.evaluate(`(async () => {
    notes[${JSON.stringify(NOTE_ID)}] = { bookId: "__sync_test__", page: 1, text: "BookBot cross-device verification", tags: ["sync-test"], updatedAt: new Date().toISOString() };
    localStorage.setItem(NOTES_STORAGE, JSON.stringify(notes));
    await saveServerProfile();
    return notes[${JSON.stringify(NOTE_ID)}]?.text || null;
  })()`);
  if (created !== "BookBot cross-device verification") throw new Error("Device A could not create the verification note");

  const received = await deviceB.client.evaluate(`(async () => { await saveServerProfile(); return notes[${JSON.stringify(NOTE_ID)}]?.text || null; })()`);
  if (received !== created) throw new Error("Device B did not receive Device A's note");

  const deleted = await deviceA.client.evaluate(`(async () => {
    delete notes[${JSON.stringify(NOTE_ID)}];
    localStorage.setItem(NOTES_STORAGE, JSON.stringify(notes));
    await saveServerProfile();
    return !notes[${JSON.stringify(NOTE_ID)}];
  })()`);
  if (!deleted) throw new Error("Device A could not delete the verification note");

  const removedOnB = await deviceB.client.evaluate(`(async () => { await saveServerProfile(); return !notes[${JSON.stringify(NOTE_ID)}]; })()`);
  if (!removedOnB) throw new Error("Device B did not receive the deletion marker");

  console.log(JSON.stringify({
    ok: true,
    devicesIndependent: true,
    noteCreatedOnA: true,
    noteReceivedOnB: true,
    noteDeletedOnA: true,
    deletionReceivedOnB: true,
  }, null, 2));
} finally {
  await closeDevice(deviceA);
  await closeDevice(deviceB);
  await delay(500);
  await rm(TEST_ROOT, { recursive: true, force: true });
}
