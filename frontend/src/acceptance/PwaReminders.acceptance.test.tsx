/**
 * E21 acceptance tests: PWA, reminders, README and the end-to-end journey (frozen with the exam commit).
 * The ICS export of the plan is covered by `backend/tests/acceptance/test_calendar_ics_acceptance.py`.
 *
 * Contract these tests drive:
 *
 * PWA (vite-plugin-pwa 2, `generateSW`)
 * - `frontend/vite.config.ts` has a named export `pwaOptions`: the very object given to `VitePWA(...)`.
 *   `strategies` is absent or `"generateSW"`, `manifestFilename` absent or `manifest.webmanifest`,
 *   `manifest.name` is `ClashPilot`, `manifest.display` is `standalone`, `manifest.icons` lists PNGs of
 *   `192x192` and `512x512` whose `src` is a file of `frontend/public` (a leading `/` allowed), with the
 *   real pixel size declared. When the service worker has a navigation fallback, its
 *   `navigateFallbackDenylist` refuses every `/api/...` path; no `runtimeCaching` rule matches `/api/...`.
 * - `frontend/public` holds no image outside `frontend/public/icons/`, no file named after Supercell or
 *   the game, no image carrying the word "Supercell" in its bytes.
 * - `vite build` (run here into a temporary folder) emits `manifest.webmanifest` (same fields), an
 *   `sw.js`, the icons, and an `index.html` linking the manifest. When `sw.js` has a navigation route it
 *   carries a `denylist` naming `api`.
 * - The app renders with `navigator.serviceWorker` removed (and without `Notification`).
 *
 * Reminders (`frontend/src/lib/reminders.ts`, no external clock: `Date.now()` and `setTimeout`)
 * - `type ReminderSettings = { enabled: boolean; leadMinutes: number; kinds: string[] }`: `kinds` are the
 *   lanes whose ends are reminded (`builder`, `lab`, `pet_house`, `blacksmith`).
 *   `DEFAULT_REMINDER_SETTINGS`: not enabled, `leadMinutes` a positive integer, all four lanes.
 *   `loadReminderSettings()` / `saveReminderSettings(settings)` use `localStorage["clashpilot.reminders"]`
 *   (JSON of the settings); absent or corrupt storage gives the defaults, never an exception.
 * - `scheduleReminders(assignments, settings, range, onInApp) => cancel`: `assignments` are the
 *   `assignments` of a plan answer (planned and frozen alike), `range = {from, to}` UTC epochs in seconds
 *   (an end counts when `from <= end <= to`), `onInApp(reminder)` receives
 *   `{taskId, name, lane, end, message}` (`message` is a French sentence naming the task).
 *   Each reminder is due at `end - leadMinutes`; when that instant has passed but the end has not, it
 *   fires at once; an end already past gives nothing. Disabled settings or a lane that is not in `kinds`
 *   give no reminder and no timer. A system notification (`new Notification(title, options)`) is created
 *   only when `Notification.permission === "granted"` when the reminder fires; otherwise (denied, default,
 *   or no `Notification` at all) `onInApp` is called. `cancel()` clears every timer. No `setTimeout`
 *   delay exceeds 2^31-1 ms: a long wait is cut into chunks.
 * - The app (Layout) schedules the reminders of `GET /api/villages/{id}/plans/latest` with the stored
 *   settings and announces an in-app reminder in a `role="status"` or `role="alert"` element.
 *
 * Réglages (`/reglages`), a section titled "Notifications" (`h2`):
 * - the sentence that reminders only work while the app is open (matched by `REMINDERS_ONLY_OPEN`);
 * - the checkbox "Activer les rappels", the input "Délai avant la fin (minutes)", a `group` named
 *   "Types d'événements" with one checkbox per lane named by `fr.lanes` ("Constructeur", "Laboratoire",
 *   "Maison des animaux", "Forgeron"). Every change is saved at once under `clashpilot.reminders`.
 *   Enabling the reminders asks `Notification.requestPermission()` when the permission is `default`.
 * - a link "Exporter vers mon agenda (.ics)" to `/api/villages/{id}/plans/latest/calendar.ics`.
 *
 * End to end (`npm run e2e` = `node scripts/e2e.mjs`): this file reads the files, it never runs Playwright.
 * - `scripts/e2e.mjs` calls `playwright test -c playwright.config.ts --project=chromium` (cwd `e2e`),
 *   runs `npm ci` and `playwright install chromium` when needed, builds the frontend, and is no longer
 *   the stub of E01.
 * - `e2e/package.json` pins `@playwright/test` to an exact 1.65.x, `e2e/package-lock.json` exists,
 *   `e2e/playwright.config.ts` has a `webServer` using `backend/.venv`, a temporary data dir and
 *   `CLASHPILOT_SOLVER_MAX_TIME_S`; `e2e/fixtures/` holds a fictional pack and a fictional export.
 * - `e2e/journey.spec.ts` has six tests, none skipped, titled with: import, que faire maintenant,
 *   calendrier, Gantt, retard d'une heure, viewport téléphone (390 px, `scrollWidth`/`clientWidth`).
 *
 * README (French): sections on installation, pack building, import, `CLASHPILOT_HOST` with its security
 * warning, the exact Fan Content Policy notice and the limits of the official API.
 */
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "../App";
import { fr } from "../i18n/fr";

// ---------------------------------------------------------------------------------- fetch stub

type Call = { method: string; path: string; body: string };
type Reply = { status?: number; body?: unknown };
type Handler = (call: Call) => Reply | Promise<Reply> | undefined;

const router = vi.hoisted(() => ({
  dispatch: (_input: unknown, _init?: unknown): Promise<Response> =>
    Promise.reject(new Error("fetch is not stubbed")),
}));
vi.hoisted(() => {
  globalThis.fetch = ((input: unknown, init?: unknown) =>
    router.dispatch(input, init)) as typeof fetch;
});

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body ?? null), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function installFetch(handler: Handler): void {
  router.dispatch = async (input, init) => {
    const request =
      input instanceof Request
        ? input
        : new Request(new URL(String(input), window.location.href), init as RequestInit);
    const call: Call = {
      method: request.method.toUpperCase(),
      path: new URL(request.url).pathname,
      body: await request.clone().text(),
    };
    const reply = await handler(call);
    if (reply === undefined) {
      const code = call.path.endsWith("/schedule") ? "schedule_not_found" : "no_plan";
      return json(404, { detail: { code, message: "Introuvable." } });
    }
    return json(reply.status ?? 200, reply.body);
  };
  vi.stubGlobal("fetch", (input: unknown, init?: unknown) => router.dispatch(input, init));
}

// -------------------------------------------------------------------------------------- fixtures

const TAG = "P2Y8QQ0";
const LATEST = `/api/villages/${TAG}/plans/latest`;
const ICS = `${LATEST}/calendar.ics`;
const LANES = ["builder", "lab", "pet_house", "blacksmith"] as const;
const STORAGE = "clashpilot.reminders";
const MAX_TIMER_MS = 2 ** 31 - 1;

const FRONTEND = fileURLToPath(new URL("../../", import.meta.url));
const ROOT = fileURLToPath(new URL("../../../", import.meta.url));
const PUBLIC = join(FRONTEND, "public");

type Assignment = {
  task_id: string;
  data_id: number;
  name: string;
  lane: string;
  lane_slot: number;
  start: number;
  end: number;
  status: "planned" | "frozen";
  requires: string[];
};

function assignment(overrides: Partial<Assignment> & Pick<Assignment, "task_id" | "name" | "end">) {
  return {
    data_id: 1000001,
    lane: "builder",
    lane_slot: 0,
    start: overrides.end - 3600,
    status: "planned",
    requires: [],
    ...overrides,
  } satisfies Assignment;
}

function planOf(assignments: Assignment[], now: number) {
  return {
    plan_id: 4,
    now,
    timezone: "Europe/Paris",
    horizon_days: 90,
    horizon_end: now + 90 * 86_400,
    engine: "auto",
    profile: "fast",
    reproducible: false,
    input_hash: "h".repeat(64),
    score: 10,
    proof: { kind: "heuristic", objective: 10, bound: null, gap_abs: null, scope: "" },
    pack: { pack_id: "pack-alpha", game_version: "7.1-test", fictional: false },
    assignments,
    excluded: [],
    warnings: [],
    stale: false,
  };
}

// ----------------------------------------------------------------------------------- helpers

function openApp(route: string): void {
  localStorage.setItem("clashpilot.village", TAG);
  window.history.pushState({}, "", route);
}

/** The text nodes of an element kept apart (`textContent` glues siblings together). */
function textOf(element: Element | null | undefined): string {
  if (!element) {
    return "";
  }
  const parts: string[] = [];
  const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node !== null; node = walker.nextNode()) {
    parts.push(node.textContent ?? "");
  }
  return parts.join(" ").replace(/\s+/g, " ").trim();
}

type SystemNotification = { title: unknown; options: unknown };

/** A stand-in for the browser `Notification` with the given permission; `created` lists the calls. */
function stubNotification(permission: "granted" | "denied" | "default") {
  const created: SystemNotification[] = [];
  const requestPermission = vi.fn(async () => permission);
  class FakeNotification {
    static permission = permission;
    static requestPermission = requestPermission;
    constructor(title: unknown, options?: unknown) {
      created.push({ title, options });
    }
  }
  vi.stubGlobal("Notification", FakeNotification);
  return { created, requestPermission };
}

function withoutNotification(): void {
  Reflect.deleteProperty(globalThis, "Notification");
  Reflect.deleteProperty(window, "Notification");
}

const mentions = (item: SystemNotification, name: string): boolean =>
  JSON.stringify([item.title, item.options]).includes(name);

function readText(path: string): string {
  const file = join(ROOT, path);
  expect(existsSync(file), `${path} is missing`).toBe(true);
  return readFileSync(file, "utf8");
}

function filesUnder(folder: string): string[] {
  if (!existsSync(folder)) {
    return [];
  }
  return readdirSync(folder, { withFileTypes: true }).flatMap((entry) =>
    entry.isDirectory() ? filesUnder(join(folder, entry.name)) : [join(folder, entry.name)],
  );
}

/** Width and height of a PNG, read from its header. */
function pngSize(file: string): { width: number; height: number } {
  const bytes = readFileSync(file);
  const signature = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  expect(bytes.subarray(0, 8).equals(signature), `${file} is not a PNG`).toBe(true);
  return { width: bytes.readUInt32BE(16), height: bytes.readUInt32BE(20) };
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  localStorage.clear();
  window.history.pushState({}, "", "/");
});

// ------------------------------------------------------------------------------------------ PWA

type IconSpec = { src: string; sizes: string; type?: string };
type Manifest = { name?: string; display?: string; icons?: IconSpec[] };
type RuntimeRule = {
  urlPattern: RegExp | string | ((options: unknown) => boolean);
};
type PwaOptions = {
  strategies?: string;
  manifestFilename?: string;
  manifest?: Manifest;
  workbox?: {
    navigateFallback?: string | null;
    navigateFallbackDenylist?: RegExp[];
    runtimeCaching?: RuntimeRule[];
  };
};

async function pwaOptions(): Promise<PwaOptions> {
  const config = (await import("../../vite.config")) as unknown as { pwaOptions?: PwaOptions };
  expect(config.pwaOptions, "vite.config.ts must export `pwaOptions`").toBeDefined();
  return config.pwaOptions as PwaOptions;
}

function iconFile(src: string): string {
  return join(PUBLIC, src.replace(/^\/+/, ""));
}

function expectManifest(manifest: Manifest | undefined): void {
  expect(manifest?.name).toBe("ClashPilot");
  expect(manifest?.display).toBe("standalone");
  const sizes = (manifest?.icons ?? []).map((icon) => icon.sizes);
  expect(sizes).toContain("192x192");
  expect(sizes).toContain("512x512");
}

describe("PWA configuration", () => {
  it("exports a generateSW configuration with the ClashPilot manifest", async () => {
    const options = await pwaOptions();

    expect(options.strategies === undefined || options.strategies === "generateSW").toBe(true);
    expect(
      options.manifestFilename === undefined || options.manifestFilename === "manifest.webmanifest",
    ).toBe(true);
    expectManifest(options.manifest);
  });

  it("declares icons that exist in public with the declared pixel size", async () => {
    const options = await pwaOptions();
    const icons = options.manifest?.icons ?? [];

    for (const wanted of [192, 512]) {
      const icon = icons.find((candidate) => candidate.sizes === `${wanted}x${wanted}`);
      expect(icon, `an icon of ${wanted}x${wanted}`).toBeDefined();
      const file = iconFile(icon?.src ?? "");
      expect(existsSync(file), `${file} is missing`).toBe(true);
      expect(file.toLowerCase().endsWith(".png")).toBe(true);
      expect(pngSize(file)).toEqual({ width: wanted, height: wanted });
    }
  });

  it("keeps Supercell images out of the public folder", () => {
    const files = filesUnder(PUBLIC);
    expect(files.length, "frontend/public must exist and hold the icons").toBeGreaterThan(0);

    for (const file of files) {
      const path = relative(PUBLIC, file).split(sep).join("/");
      const isImage = /\.(png|jpe?g|gif|webp|svg|ico|avif|bmp)$/i.test(path);
      if (isImage) {
        expect(path.startsWith("icons/"), `${path}: images belong in public/icons`).toBe(true);
        expect(
          readFileSync(file).toString("latin1"),
          `${path} carries a Supercell mark`,
        ).not.toMatch(/supercell/i);
      }
      expect(path, `${path} is named after Supercell or the game`).not.toMatch(
        /supercell|clash[-_ ]?of[-_ ]?clans|\bcoc\b|coc[-_]/i,
      );
    }
  });

  it("never lets the service worker answer /api", async () => {
    const workbox = (await pwaOptions()).workbox ?? {};
    const samples = [
      "/api/villages/P2Y8QQ0/plans/latest",
      "/api/villages/P2Y8QQ0/plans/latest/calendar.ics",
      "/api/meta/engines",
    ];

    if (workbox.navigateFallback !== undefined && workbox.navigateFallback !== null) {
      const denylist = workbox.navigateFallbackDenylist ?? [];
      for (const sample of samples) {
        expect(
          denylist.some((pattern) => pattern.test(sample)),
          `${sample} must be denied by navigateFallbackDenylist`,
        ).toBe(true);
      }
    }
    for (const rule of workbox.runtimeCaching ?? []) {
      for (const sample of samples) {
        const url = new URL(sample, "https://localhost");
        const pattern = rule.urlPattern;
        const matches =
          pattern instanceof RegExp
            ? pattern.test(url.href) || pattern.test(sample)
            : typeof pattern === "string"
              ? url.href.includes(pattern) || sample === pattern
              : pattern({
                  url,
                  sameOrigin: true,
                  request: { destination: "", mode: "cors", url: url.href },
                });
        expect(matches, `a runtimeCaching rule matches ${sample}`).toBe(false);
      }
    }
  });
});

describe("PWA build", () => {
  it("emits the manifest, a service worker and the icons", { timeout: 150_000 }, () => {
    const out = mkdtempSync(join(tmpdir(), "clashpilot-pwa-"));
    try {
      const env: NodeJS.ProcessEnv = { ...process.env, NODE_ENV: "production" };
      delete env.VITEST;
      const build = spawnSync(
        process.execPath,
        [
          join(FRONTEND, "node_modules", "vite", "bin", "vite.js"),
          "build",
          "--outDir",
          out,
          "--emptyOutDir",
        ],
        { cwd: FRONTEND, encoding: "utf8", env, timeout: 140_000 },
      );
      expect(build.status, `${build.stdout}\n${build.stderr}`).toBe(0);

      const manifestFile = join(out, "manifest.webmanifest");
      expect(existsSync(manifestFile), "manifest.webmanifest is not emitted").toBe(true);
      const manifest = JSON.parse(readFileSync(manifestFile, "utf8")) as Manifest;
      expectManifest(manifest);
      for (const icon of manifest.icons ?? []) {
        const emitted = join(out, icon.src.replace(/^\/+/, ""));
        expect(existsSync(emitted), `${icon.src} is not emitted`).toBe(true);
        const wanted = Number.parseInt(icon.sizes, 10);
        expect(pngSize(emitted)).toEqual({ width: wanted, height: wanted });
      }

      expect(existsSync(join(out, "sw.js")), "sw.js is not emitted").toBe(true);
      const worker = readFileSync(join(out, "sw.js"), "utf8");
      if (/NavigationRoute/.test(worker)) {
        expect(worker, "the navigation route must deny /api").toMatch(/denylist\s*:\s*\[[^\]]*api/);
      }
      expect(worker).not.toMatch(/url\s*:\s*["']\/?api\//);

      const html = readFileSync(join(out, "index.html"), "utf8");
      expect(html).toMatch(
        /<link[^>]+rel="manifest"[^>]*manifest\.webmanifest|<link[^>]+manifest\.webmanifest[^>]*rel="manifest"/,
      );
    } finally {
      rmSync(out, { recursive: true, force: true });
    }
  });
});

describe("without navigator.serviceWorker", () => {
  it.each([
    ["/", fr.app.title],
    ["/reglages", "Notifications"],
  ])("renders %s without error", async (route, heading) => {
    Reflect.deleteProperty(navigator, "serviceWorker");
    Reflect.deleteProperty(Object.getPrototypeOf(navigator), "serviceWorker");
    expect("serviceWorker" in navigator).toBe(false);
    withoutNotification();
    const failures: unknown[] = [];
    const record = (event: ErrorEvent) => failures.push(event.error ?? event.message);
    window.addEventListener("error", record);
    const errors = vi.spyOn(console, "error").mockImplementation(() => undefined);
    installFetch(() => undefined);
    openApp(route);

    try {
      render(<App />);
      expect(await screen.findByRole("heading", { name: heading })).toBeTruthy();
      expect(failures).toEqual([]);
      expect(errors.mock.calls.map((call) => String(call[0])).join("\n")).not.toMatch(
        /serviceWorker/i,
      );
    } finally {
      window.removeEventListener("error", record);
      errors.mockRestore();
    }
  });
});

// ------------------------------------------------------------------------------------- reminders

type ReminderSettings = { enabled: boolean; leadMinutes: number; kinds: string[] };
type Reminder = { taskId: string; name: string; lane: string; end: number; message: string };
type RemindersModule = {
  DEFAULT_REMINDER_SETTINGS: ReminderSettings;
  loadReminderSettings: () => ReminderSettings;
  saveReminderSettings: (settings: ReminderSettings) => void;
  scheduleReminders: (
    assignments: Assignment[],
    settings: ReminderSettings,
    range: { from: number; to: number },
    onInApp: (reminder: Reminder) => void,
  ) => () => void;
};

async function reminders(): Promise<RemindersModule> {
  // A variable path keeps Vite from resolving the module while the test file is transformed.
  const path = "../lib/reminders";
  return (await import(/* @vite-ignore */ path)) as RemindersModule;
}

const NOW = Date.UTC(2026, 10, 12, 10, 0, 0) / 1000;
const DAY = 86_400;
const ALL_KINDS = [...LANES];

const CANON = assignment({ task_id: "1000001:1:5", name: "Canon d'essai", end: NOW + 3600 });
const BARBARE = assignment({
  task_id: "4000001:0:6",
  data_id: 4000001,
  name: "Barbare d'essai",
  lane: "lab",
  end: NOW + 7200,
});
const MORTIER = assignment({
  task_id: "1000002:1:3",
  data_id: 1000002,
  name: "Mortier d'essai",
  status: "frozen",
  end: NOW + 1800,
});
const ANIMAL = assignment({
  task_id: "5000001:0:2",
  data_id: 5000001,
  name: "Animal d'essai",
  lane: "pet_house",
  end: NOW + 2 * DAY,
});
const ANCIEN = assignment({ task_id: "1000009:1:2", name: "Ancien mur", end: NOW - 600 });
const PLAN_ENDS = [CANON, BARBARE, MORTIER, ANIMAL, ANCIEN];
const TODAY = { from: NOW, to: NOW + DAY };

const settings = (overrides: Partial<ReminderSettings> = {}): ReminderSettings => ({
  enabled: true,
  leadMinutes: 10,
  kinds: ALL_KINDS,
  ...overrides,
});

function startClock(): void {
  vi.useFakeTimers();
  vi.setSystemTime(NOW * 1000);
}

const at = (seconds: number): number => seconds * 1000;

describe("reminder settings", () => {
  it("defaults to disabled, a positive lead time and every lane", async () => {
    const { DEFAULT_REMINDER_SETTINGS, loadReminderSettings } = await reminders();

    expect(DEFAULT_REMINDER_SETTINGS.enabled).toBe(false);
    expect(Number.isInteger(DEFAULT_REMINDER_SETTINGS.leadMinutes)).toBe(true);
    expect(DEFAULT_REMINDER_SETTINGS.leadMinutes).toBeGreaterThan(0);
    expect([...DEFAULT_REMINDER_SETTINGS.kinds].sort()).toEqual([...LANES].sort());
    expect(loadReminderSettings()).toEqual(DEFAULT_REMINDER_SETTINGS);
  });

  it("round-trips through localStorage and survives corrupt content", async () => {
    const { DEFAULT_REMINDER_SETTINGS, loadReminderSettings, saveReminderSettings } =
      await reminders();
    const mine = settings({ leadMinutes: 25, kinds: ["builder", "lab"] });

    saveReminderSettings(mine);

    expect(loadReminderSettings()).toEqual(mine);
    expect(JSON.parse(localStorage.getItem(STORAGE) ?? "null")).toEqual(mine);
    localStorage.setItem(STORAGE, "{not json");
    expect(loadReminderSettings()).toEqual(DEFAULT_REMINDER_SETTINGS);
  });
});

describe("scheduleReminders", () => {
  it("reminds each end of the range once, lead time before it, in the app", async () => {
    const { scheduleReminders } = await reminders();
    const { created } = stubNotification("default");
    startClock();
    const seen: Reminder[] = [];

    scheduleReminders(PLAN_ENDS, settings(), TODAY, (reminder) => seen.push(reminder));

    // Mortier (frozen) ends at +30 min: due at +20 min; Canon at +60: due +50; Barbare at +120: due +110.
    vi.advanceTimersByTime(at(20 * 60 - 1));
    expect(seen).toEqual([]);
    vi.advanceTimersByTime(at(1));
    expect(seen.map((r) => r.taskId)).toEqual([MORTIER.task_id]);
    vi.advanceTimersByTime(at(30 * 60 - 1));
    expect(seen).toHaveLength(1);
    vi.advanceTimersByTime(at(1));
    expect(seen.map((r) => r.taskId)).toEqual([MORTIER.task_id, CANON.task_id]);
    vi.advanceTimersByTime(at(DAY));

    // The task beyond the range (Animal) and the past one (Ancien mur) never remind.
    expect(seen.map((r) => r.taskId)).toEqual([MORTIER.task_id, CANON.task_id, BARBARE.task_id]);
    for (const reminder of seen) {
      const source = PLAN_ENDS.find((a) => a.task_id === reminder.taskId);
      expect(reminder.name).toBe(source?.name);
      expect(reminder.lane).toBe(source?.lane);
      expect(reminder.end).toBe(source?.end);
      expect(reminder.message).toContain(reminder.name);
    }
    expect(created).toEqual([]);
  });

  it("creates a system notification only when the permission is granted", async () => {
    const { scheduleReminders } = await reminders();
    const { created } = stubNotification("granted");
    startClock();

    scheduleReminders([CANON, BARBARE], settings(), TODAY, () => undefined);
    vi.advanceTimersByTime(at(DAY));

    expect(created).toHaveLength(2);
    expect(mentions(created[0] as SystemNotification, CANON.name)).toBe(true);
    expect(mentions(created[1] as SystemNotification, BARBARE.name)).toBe(true);
  });

  it.each(["denied", "default"] as const)(
    "falls back to the in-app message when the permission is %s",
    async (permission) => {
      const { scheduleReminders } = await reminders();
      const { created } = stubNotification(permission);
      startClock();
      const seen: Reminder[] = [];

      scheduleReminders([CANON, BARBARE], settings(), TODAY, (r) => seen.push(r));
      vi.advanceTimersByTime(at(DAY));

      expect(created).toEqual([]);
      expect(seen.map((r) => r.name)).toEqual([CANON.name, BARBARE.name]);
    },
  );

  it("falls back to the in-app message when the browser has no Notification", async () => {
    const { scheduleReminders } = await reminders();
    withoutNotification();
    startClock();
    const seen: Reminder[] = [];

    scheduleReminders([CANON], settings(), TODAY, (r) => seen.push(r));
    vi.advanceTimersByTime(at(DAY));

    expect(seen.map((r) => r.taskId)).toEqual([CANON.task_id]);
  });

  it("does nothing when the reminders are disabled", async () => {
    const { scheduleReminders } = await reminders();
    const { created } = stubNotification("granted");
    startClock();
    const seen: Reminder[] = [];

    scheduleReminders(PLAN_ENDS, settings({ enabled: false }), TODAY, (r) => seen.push(r));

    expect(vi.getTimerCount()).toBe(0);
    vi.advanceTimersByTime(at(DAY));
    expect(seen).toEqual([]);
    expect(created).toEqual([]);
  });

  it("gives nothing for a lane that is not chosen", async () => {
    const { scheduleReminders } = await reminders();
    stubNotification("default");
    startClock();
    const seen: Reminder[] = [];

    scheduleReminders(PLAN_ENDS, settings({ kinds: ["lab"] }), TODAY, (r) => seen.push(r));
    vi.advanceTimersByTime(at(DAY));
    expect(seen.map((r) => r.taskId)).toEqual([BARBARE.task_id]);

    seen.length = 0;
    scheduleReminders(PLAN_ENDS, settings({ kinds: [] }), TODAY, (r) => seen.push(r));
    expect(vi.getTimerCount()).toBe(0);
    vi.advanceTimersByTime(at(DAY));
    expect(seen).toEqual([]);
  });

  it("fires at once when the lead time has already passed but the end has not", async () => {
    const { scheduleReminders } = await reminders();
    stubNotification("default");
    startClock();
    const seen: Reminder[] = [];

    // Mortier ends in 30 minutes: with 45 minutes of lead time it was due 15 minutes ago.
    scheduleReminders(
      [MORTIER, ANCIEN],
      settings({ leadMinutes: 45 }),
      { from: NOW - DAY, to: NOW + DAY },
      (r) => seen.push(r),
    );
    vi.advanceTimersByTime(1);

    expect(seen.map((r) => r.taskId)).toEqual([MORTIER.task_id]);
  });

  it("uses the lead time of the settings", async () => {
    const { scheduleReminders } = await reminders();
    stubNotification("default");
    startClock();
    const seen: Reminder[] = [];

    scheduleReminders([BARBARE], settings({ leadMinutes: 90 }), TODAY, (r) => seen.push(r));
    vi.advanceTimersByTime(at(30 * 60 - 1));
    expect(seen).toEqual([]);
    vi.advanceTimersByTime(at(1));
    expect(seen).toHaveLength(1);
  });

  it("stops everything when cancelled", async () => {
    const { scheduleReminders } = await reminders();
    stubNotification("granted");
    startClock();
    const seen: Reminder[] = [];

    const cancel = scheduleReminders(PLAN_ENDS, settings(), TODAY, (r) => seen.push(r));
    expect(vi.getTimerCount()).toBeGreaterThan(0);
    cancel();

    expect(vi.getTimerCount()).toBe(0);
    vi.advanceTimersByTime(at(2 * DAY));
    expect(seen).toEqual([]);
  });

  it("cuts a long wait into timers of at most 2^31-1 ms and still fires on time", async () => {
    const { scheduleReminders } = await reminders();
    stubNotification("default");
    startClock();
    const delays: number[] = [];
    const real = globalThis.setTimeout;
    const spy = vi.spyOn(globalThis, "setTimeout").mockImplementation(((
      handler: TimerHandler,
      delay?: number,
      ...args: unknown[]
    ) => {
      delays.push(delay ?? 0);
      return real(handler as () => void, delay, ...args);
    }) as typeof setTimeout);
    const far = assignment({
      task_id: "1000010:1:9",
      name: "Hôtel de ville d'essai",
      end: NOW + 40 * DAY,
    });
    const seen: Reminder[] = [];

    scheduleReminders([far], settings(), { from: NOW, to: NOW + 41 * DAY }, (r) => seen.push(r));
    const due = 40 * DAY - 10 * 60;
    vi.advanceTimersByTime(at(due) - 1);
    expect(seen).toEqual([]);
    vi.advanceTimersByTime(1);
    spy.mockRestore();

    expect(seen.map((r) => r.taskId)).toEqual([far.task_id]);
    expect(delays.length).toBeGreaterThan(1);
    expect(Math.max(...delays)).toBeLessThanOrEqual(MAX_TIMER_MS);
    vi.advanceTimersByTime(at(2 * DAY));
    expect(seen).toHaveLength(1);
  });
});

describe("reminders in the app", () => {
  /** Plan whose only upcoming end is due about two seconds from now (one minute of lead time). */
  function plan(): ReturnType<typeof planOf> {
    const now = Math.floor(Date.now() / 1000);
    return planOf(
      [
        assignment({ task_id: "1000001:1:5", name: "Canon d'essai", end: now + 62 }),
        assignment({ task_id: "1000009:1:2", name: "Ancien mur", end: now - 3600 }),
      ],
      now,
    );
  }

  const withPlan: Handler = (call) =>
    call.method === "GET" && call.path === LATEST ? { body: plan() } : undefined;

  function announced(name: string): boolean {
    return [...document.querySelectorAll('[role="status"], [role="alert"]')].some((element) =>
      textOf(element).includes(name),
    );
  }

  beforeEach(() => {
    localStorage.setItem(STORAGE, JSON.stringify(settings({ leadMinutes: 1 })));
  });

  it("announces the reminder in the page when the permission is not granted", {
    timeout: 15_000,
  }, async () => {
    const { created } = stubNotification("default");
    installFetch(withPlan);
    openApp("/");

    render(<App />);

    await waitFor(() => expect(announced("Canon d'essai")).toBe(true), { timeout: 8000 });
    expect(announced("Ancien mur")).toBe(false);
    expect(created).toEqual([]);
  });

  it("creates a system notification when the permission is granted", {
    timeout: 15_000,
  }, async () => {
    const { created } = stubNotification("granted");
    installFetch(withPlan);
    openApp("/");

    render(<App />);

    await waitFor(() => expect(created.length).toBeGreaterThan(0), { timeout: 8000 });
    expect(created.some((item) => mentions(item, "Canon d'essai"))).toBe(true);
    expect(created.some((item) => mentions(item, "Ancien mur"))).toBe(false);
  });
});

// --------------------------------------------------------------------------------------- Réglages

const REMINDERS_ONLY_OPEN = /rappels[^.]*\b(que|uniquement|seulement)\b[^.]*ouverte/i;

function remindersSection(): HTMLElement {
  const heading = screen.getByRole("heading", { name: "Notifications" });
  const section = heading.closest("section") ?? heading.parentElement;
  expect(section).not.toBeNull();
  return section as HTMLElement;
}

function stored(): ReminderSettings {
  return JSON.parse(localStorage.getItem(STORAGE) ?? "null") as ReminderSettings;
}

describe("Réglages: Notifications", () => {
  it("says the reminders only work while the app is open", async () => {
    stubNotification("default");
    installFetch(() => undefined);
    openApp("/reglages");

    render(<App />);

    await screen.findByRole("heading", { name: "Notifications" });
    expect(textOf(remindersSection())).toMatch(REMINDERS_ONLY_OPEN);
  });

  it("offers the calendar export of the latest plan", async () => {
    stubNotification("default");
    installFetch(() => undefined);
    openApp("/reglages");

    render(<App />);

    const link = await screen.findByRole("link", { name: "Exporter vers mon agenda (.ics)" });
    expect(link.getAttribute("href")?.endsWith(ICS)).toBe(true);
  });

  it("saves the reminder settings at once and shows them again after a reload", async () => {
    const { requestPermission } = stubNotification("default");
    installFetch(() => undefined);
    openApp("/reglages");
    const user = userEvent.setup();

    const first = render(<App />);
    await screen.findByRole("heading", { name: "Notifications" });
    const section = within(remindersSection());
    const enable = section.getByRole("checkbox", {
      name: "Activer les rappels",
    }) as HTMLInputElement;
    expect(enable.checked).toBe(false);

    await user.click(enable);
    expect(stored().enabled).toBe(true);
    expect(requestPermission).toHaveBeenCalledTimes(1);

    const lead = section.getByLabelText("Délai avant la fin (minutes)");
    await user.clear(lead);
    await user.type(lead, "30");
    expect(stored().leadMinutes).toBe(30);

    const kinds = within(section.getByRole("group", { name: "Types d'événements" }));
    for (const lane of LANES) {
      const box = kinds.getByRole("checkbox", { name: fr.lanes[lane] }) as HTMLInputElement;
      expect(box.checked, fr.lanes[lane]).toBe(true);
    }
    await user.click(kinds.getByRole("checkbox", { name: fr.lanes.lab }));
    expect([...stored().kinds].sort()).toEqual(["blacksmith", "builder", "pet_house"]);

    first.unmount();
    render(<App />);
    await screen.findByRole("heading", { name: "Notifications" });
    const again = within(remindersSection());
    expect(
      (again.getByRole("checkbox", { name: "Activer les rappels" }) as HTMLInputElement).checked,
    ).toBe(true);
    expect((again.getByLabelText("Délai avant la fin (minutes)") as HTMLInputElement).value).toBe(
      "30",
    );
    const lab = again.getByRole("checkbox", { name: fr.lanes.lab }) as HTMLInputElement;
    expect(lab.checked).toBe(false);
  });

  it("works without the Notification API", async () => {
    withoutNotification();
    installFetch(() => undefined);
    openApp("/reglages");
    const user = userEvent.setup();

    render(<App />);
    await screen.findByRole("heading", { name: "Notifications" });
    await user.click(
      within(remindersSection()).getByRole("checkbox", { name: "Activer les rappels" }),
    );

    expect(stored().enabled).toBe(true);
  });
});

// ------------------------------------------------------------------------------- end-to-end files

describe("end-to-end journey files", () => {
  const plain = (text: string): string =>
    text.normalize("NFD").replace(/\p{M}/gu, "").replace(/[’‘]/g, "'").toLowerCase();

  it("runs Playwright on chromium from scripts/e2e.mjs", () => {
    const script = readText("scripts/e2e.mjs");

    expect(script).not.toMatch(/does not exist yet/);
    expect(script).toMatch(/playwright test/);
    expect(script).toMatch(/-c playwright\.config\.ts/);
    expect(script).toMatch(/--project=chromium/);
    expect(script).toMatch(/playwright install chromium/);
    expect(script).toMatch(/npm ci/);
    expect(script).toMatch(/npm run build|vite build/);
    expect(script).toMatch(/--list|process\.argv/);
  });

  it("has a pinned Playwright package, a lock file and a web server configuration", () => {
    const pkg = JSON.parse(readText("e2e/package.json")) as {
      devDependencies?: Record<string, string>;
      dependencies?: Record<string, string>;
    };
    const version = { ...pkg.dependencies, ...pkg.devDependencies }["@playwright/test"];
    expect(version, "@playwright/test must be pinned to an exact 1.65.x").toMatch(/^1\.65\.\d+$/);
    expect(JSON.parse(readText("e2e/package-lock.json"))).toHaveProperty("lockfileVersion");

    const config = readText("e2e/playwright.config.ts");
    expect(config).toMatch(/webServer/);
    expect(config).toMatch(/chromium/);
    expect(config).toMatch(/CLASHPILOT_SOLVER_MAX_TIME_S/);
    expect(config).toMatch(/CLASHPILOT_DATA_DIR/);
    expect(config).toMatch(/\.venv/);
  });

  it("keeps fictional fixtures only", () => {
    const folder = join(ROOT, "e2e", "fixtures");
    const files = filesUnder(folder).map((file) => relative(folder, file));
    expect(files.filter((file) => /\.json$/i.test(file)).length).toBeGreaterThanOrEqual(2);
    expect(files.filter((file) => /(^|[\\/])\.env|secret|token/i.test(file))).toEqual([]);
    const pack = files.find((file) => /pack/i.test(file) && /\.json$/i.test(file));
    expect(pack, "a pack fixture").toBeDefined();
    expect(JSON.parse(readFileSync(join(folder, pack ?? ""), "utf8"))).toMatchObject({
      fictional: true,
    });
  });

  it("names the six scenarios as tests, none skipped", () => {
    const spec = readText("e2e/journey.spec.ts");
    const titles = [...spec.matchAll(/\btest(?:\.\w+)?\(\s*(["'`])((?:\\.|(?!\1)[^\\])*)\1/g)].map(
      (match) => plain(match[2] ?? ""),
    );

    expect(spec).toMatch(/@playwright\/test/);
    expect(spec).not.toMatch(/\btest\.(skip|fixme|only)\b|\.skip\(|\.only\(/);
    const scenarios: [string, RegExp][] = [
      ["import", /\bimport/],
      ["que faire maintenant", /que faire maintenant/],
      ["calendrier", /calendrier/],
      ["Gantt", /gantt/],
      ["retard d'une heure", /retard d'une heure/],
      ["viewport téléphone", /viewport telephone/],
    ];
    for (const [name, pattern] of scenarios) {
      expect(
        titles.some((title) => pattern.test(title)),
        `a test titled "${name}"`,
      ).toBe(true);
    }
    expect(titles.length).toBeGreaterThanOrEqual(6);
    expect(spec).toMatch(/390/);
    expect(spec).toMatch(/scrollWidth/);
    expect(spec).toMatch(/clientWidth/);
  });
});

// ------------------------------------------------------------------------------------------ README

describe("README", () => {
  const readme = () => readText("README.md");

  function sections(): { heading: string; body: string }[] {
    const parts = readme().split(/^(#{1,6} .*)$/m);
    const found: { heading: string; body: string }[] = [];
    for (let i = 1; i < parts.length; i += 2) {
      found.push({ heading: parts[i] ?? "", body: parts[i + 1] ?? "" });
    }
    return found;
  }

  const sectionTitled = (pattern: RegExp) => sections().find((s) => pattern.test(s.heading));

  it("describes the installation", () => {
    const section = sectionTitled(/installation/i);
    expect(section, "an Installation section").toBeDefined();
    expect(section?.body).toContain("npm run setup");
  });

  it("describes how to build the pack", () => {
    const section = sectionTitled(/pack/i);
    expect(section, "a section about the pack").toBeDefined();
    expect(section?.body).toContain("clashpilot.gamedata.builder");
  });

  it("describes the import of a village export", () => {
    const section = sectionTitled(/import/i);
    expect(section, "a section about the import").toBeDefined();
    expect(section?.body).toMatch(/export/i);
  });

  it("explains CLASHPILOT_HOST with its security warning", () => {
    const section = sections().find((s) => s.body.includes("CLASHPILOT_HOST"));
    expect(section, "a section using CLASHPILOT_HOST").toBeDefined();
    expect(section?.body).toContain("0.0.0.0");
    expect(section?.body).toMatch(/authentification/i);
    expect(`${section?.heading}\n${section?.body}`).toMatch(
      /attention|avertissement|sécurité|securite/i,
    );
  });

  it("carries the exact Fan Content Policy notice", () => {
    const flat = (text: string) =>
      text
        .replace(/^\s*>\s?/gm, "")
        .replace(/[*_`]/g, "")
        .replace(/\s+/g, " ");

    expect(flat(readme())).toContain(flat(fr.footer.fanContentPolicy));
  });

  it("states the limits of the official API", () => {
    const section = sectionTitled(/api officielle/i);
    expect(section, "a section about the official API").toBeDefined();
    const text = `${section?.heading}\n${section?.body}`;
    expect(text).toMatch(/limite/i);
    expect(text).toContain("CLASHPILOT_COC_TOKEN");
    for (const word of [/troupes/i, /héros/i, /sorts/i, /équipement/i]) {
      expect(text).toMatch(word);
    }
  });
});
