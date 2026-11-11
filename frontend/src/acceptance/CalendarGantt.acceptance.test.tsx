/**
 * E14 acceptance tests: calendar, builders Gantt and task sheet (frozen with the exam commit).
 *
 * Contract of the UI these tests drive (everything is French, labels come from `fr.ts`):
 * - The current village is the one kept by the app in `localStorage["clashpilot.village"]`; the tests set
 *   it, render `App` at the route and read the page. The plan is `GET /api/villages/{id}/plans/latest`
 *   (404 `no_plan` = no plan); the sleep and absence bands come from `GET .../schedule/preview` (any
 *   `days` query; 404 `schedule_not_found` = no band). Dates are UTC epochs; every local date or time is
 *   computed with `Intl.DateTimeFormat` and the `timezone` of the plan, never the browser's own zone.
 * - "Today" is the system clock (the tests fake `Date` only, at the plan's `now`).
 * - Without a plan, the `main` region of each page below shows the words "Aucun plan" and a link named
 *   "Que faire maintenant ?" to `/maintenant`.
 *
 * Calendrier (`/calendrier`): one week, Monday to Sunday in the plan's zone, starting on the week of today.
 * - Exactly seven day columns, each an element carrying `data-day="YYYY-MM-DD"` (its local date).
 * - Each assignment whose `end` falls in the shown week appears once, by its name, inside the column of
 *   the local day of its `end` (starts are not shown), with the local time of the end; an assignment
 *   ending in another week is in no column.
 * - The buttons "Semaine précédente" and "Semaine suivante" show the week before / after; "Aujourd'hui"
 *   shows the week of today again.
 *
 * Constructeurs (`/constructeurs`): a Gantt diagram drawn by hand (SVG or CSS) and an equivalent table.
 * - One row per `(lane, lane_slot)` pair of the assignments: an element carrying `data-lane` and
 *   `data-lane-slot`; only these rows carry both attributes.
 * - One block per assignment, inside its row: an element carrying `data-task-id`, `data-start` and
 *   `data-end` (the epochs of the API) and `data-status` (`planned` or `frozen`, the API status). Its
 *   accessible text (aria-label, aria-labelledby or text content) says "En cours" for frozen and "Prévu"
 *   for planned. It is focusable (`tabindex` 0 or a native control).
 * - Horizontal geometry: either inline `style.left` / `style.width` in percent of the time axis, or the
 *   numeric SVG attributes `x` / `width` of the element (or of its first `rect` descendant). The axis is
 *   linear in time and covers every assignment from its start to its end. The left edge is the start; a very
 *   short block may be drawn wider than its duration (minimum visible width), never moved.
 * - Bands: each sleep and absence interval of the preview inside the axis is an element carrying
 *   `data-band="sleep"` or `data-band="absence"` with its `data-start` / `data-end` epochs and the same
 *   geometry rules on the same axis. Only blocks and bands carry `data-start`.
 * - Focusing a block or hovering it shows a `role="tooltip"` element (within 2 s) with the assignment's
 *   name and its local start and end times.
 * - Next to the diagram, a `role="table"` (a native table is fine) with the column headers
 *   "Constructeur", "Nom", "Début", "Fin" and one row per assignment: the lane as "Constructeur N" for
 *   `builder` and "Laboratoire N" for `lab` (N = `lane_slot` + 1), the name, and the local start and end
 *   (day of month and time).
 *
 * Fiche d'une tâche (`/taches/{task_id}`, the id URL-encoded or not): a heading with the assignment's
 * name and a list named "Prérequis" (aria-label or aria-labelledby) holding one item per id of its
 * `requires`; each item is a link, named after the required assignment, to that task's own sheet.
 */
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "../App";

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

let calls: Call[] = [];

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body ?? null), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function installFetch(handler: Handler): void {
  calls = [];
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
    calls.push(call);
    const reply = await handler(call);
    if (reply === undefined) {
      const missing = call.path === PREVIEW ? "schedule_not_found" : "no_plan";
      return json(404, { detail: { code: missing, message: "Introuvable." } });
    }
    return json(reply.status ?? 200, reply.body);
  };
  vi.stubGlobal("fetch", (input: unknown, init?: unknown) => router.dispatch(input, init));
}

// -------------------------------------------------------------------------------------- fixtures

const TAG = "P2Y8QQ0";
const LATEST = `/api/villages/${TAG}/plans/latest`;
const PREVIEW = `/api/villages/${TAG}/schedule/preview`;
const ZONE = "Asia/Tokyo"; // UTC+9, no daylight saving time

const utc = (day: number, hour: number, minute = 0): number =>
  Date.UTC(2026, 10, day, hour, minute, 0) / 1000;

// Wednesday 2026-11-11 12:00 in Tokyo; the week shown first runs from Monday 9 to Sunday 15.
const NOW = utc(11, 3);
const THIS_WEEK = ["09", "10", "11", "12", "13", "14", "15"].map((d) => `2026-11-${d}`);
const NEXT_WEEK = ["16", "17", "18", "19", "20", "21", "22"].map((d) => `2026-11-${d}`);
const PREVIOUS_WEEK = ["02", "03", "04", "05", "06", "07", "08"].map((d) => `2026-11-${d}`);

const PACK = { pack_id: "pack-alpha", game_version: "7.1-test", fictional: false };
const HEURISTIC = { kind: "heuristic", objective: 10, bound: null, gap_abs: null, scope: "" };

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

const CANON: Assignment = {
  // Running: 10:00 on the 11th to 08:30 on the 13th in Tokyo (23:30 UTC on the 12th).
  task_id: "1000001:1:5",
  data_id: 1000001,
  name: "Canon d'essai",
  lane: "builder",
  lane_slot: 0,
  start: utc(11, 1),
  end: utc(12, 23, 30),
  status: "frozen",
  requires: [],
};
const MORTIER: Assignment = {
  // 13:00 to 18:00 on the 11th in Tokyo.
  task_id: "1000002:1:3",
  data_id: 1000002,
  name: "Mortier d'essai",
  lane: "builder",
  lane_slot: 1,
  start: utc(11, 4),
  end: utc(11, 9),
  status: "planned",
  requires: [],
};
const MUR: Assignment = {
  // One minute long: 15:00 to 15:01 on the 12th in Tokyo.
  task_id: "1000004:7:2",
  data_id: 1000004,
  name: "Mur d'essai",
  lane: "builder",
  lane_slot: 2,
  start: utc(12, 6),
  end: utc(12, 6, 1),
  status: "planned",
  requires: [],
};
const TOUR: Assignment = {
  // 15:01 on the 12th to 01:00 on the 14th in Tokyo (still the 13th in UTC and in Europe/Paris).
  task_id: "1000003:1:4",
  data_id: 1000003,
  name: "Tour d'archers d'essai",
  lane: "builder",
  lane_slot: 1,
  start: utc(12, 6, 1),
  end: utc(13, 16),
  status: "planned",
  requires: [MORTIER.task_id, MUR.task_id],
};
const BARBARE: Assignment = {
  // Ends at 00:30 on Monday the 16th in Tokyo: next week (still Sunday the 15th in UTC and in Paris).
  task_id: "4000001:0:6",
  data_id: 4000001,
  name: "Barbare d'essai",
  lane: "lab",
  lane_slot: 0,
  start: utc(11, 3, 30),
  end: utc(15, 15, 30),
  status: "planned",
  requires: [],
};
const ASSIGNMENTS = [CANON, MORTIER, MUR, TOUR, BARBARE];

const SLEEP: [number, number][] = [
  [utc(11, 14), utc(11, 22)],
  [utc(12, 14), utc(12, 22)],
];
const ABSENCE: [number, number][] = [[utc(13, 0), utc(13, 6)]];

function plan() {
  return {
    plan_id: 8,
    now: NOW,
    timezone: ZONE,
    horizon_days: 90,
    horizon_end: NOW + 90 * 86_400,
    engine: "auto",
    profile: "fast",
    reproducible: false,
    input_hash: "h".repeat(64),
    score: 10,
    proof: HEURISTIC,
    pack: PACK,
    assignments: ASSIGNMENTS,
    excluded: [],
    warnings: [],
    stale: false,
  };
}

const PREVIEW_BODY = {
  tz: ZONE,
  sleep: SLEEP,
  work: [[utc(12, 0), utc(12, 8)]],
  absence: ABSENCE,
  available: [],
  preferred: [],
};

const withPlan: Handler = (call) => {
  if (call.method === "GET" && call.path === LATEST) {
    return { body: plan() };
  }
  if (call.method === "GET" && call.path === PREVIEW) {
    return { body: PREVIEW_BODY };
  }
  return undefined;
};

// ----------------------------------------------------------------------------------- helpers

function open(route: string): void {
  localStorage.setItem("clashpilot.village", TAG);
  window.history.pushState({}, "", route);
}

/** A wall-clock time such as 08:30 or 8h30, not glued to other digits. */
function clock(hour: number, minute: number): RegExp {
  const mm = String(minute).padStart(2, "0");
  return new RegExp(`(?<![0-9])0?${hour}\\s*[:h]\\s*${mm}(?![0-9])`);
}

/**
 * The text of an element with its text nodes kept apart: `textContent` glues sibling cells or spans
 * together ("10:0013/11"), which would hide a time from `clock`.
 */
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

function dayColumns(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>("[data-day]"));
}

async function shownDays(expected: string[]): Promise<void> {
  await waitFor(() => expect(dayColumns().map((c) => c.dataset.day)).toEqual(expected));
}

function column(day: string): HTMLElement {
  const found = dayColumns().find((c) => c.dataset.day === day);
  if (found === undefined) {
    throw new Error(`no day column for ${day}`);
  }
  return found;
}

/** The day columns whose text holds the name. */
function columnsOf(name: string): string[] {
  return dayColumns()
    .filter((c) => textOf(c).includes(name))
    .map((c) => c.dataset.day ?? "");
}

function blocks(): HTMLElement[] {
  return Array.from(document.querySelectorAll<HTMLElement>("[data-task-id][data-start][data-end]"));
}

async function blockOf(task: Assignment): Promise<HTMLElement> {
  await waitFor(() => expect(blocks().length).toBeGreaterThan(0));
  const found = blocks().filter((b) => b.dataset.taskId === task.task_id);
  expect(found).toHaveLength(1);
  return found[0] as HTMLElement;
}

function bands(kind: string): HTMLElement[] {
  return Array.from(
    document.querySelectorAll<HTMLElement>(`[data-band="${kind}"][data-start][data-end]`),
  );
}

type Geometry = { x: number; w: number };

function numberIn(value: string | null, what: string): number {
  const parsed = Number.parseFloat(value ?? "");
  if (!Number.isFinite(parsed)) {
    throw new Error(`${what} is not a number: ${value}`);
  }
  return parsed;
}

function geometry(element: Element): Geometry {
  const style = (element as HTMLElement).style;
  if (style?.left?.endsWith("%") && style.width.endsWith("%")) {
    return { x: numberIn(style.left, "left"), w: numberIn(style.width, "width") };
  }
  const shape = element.hasAttribute("x") ? element : element.querySelector("rect[x]");
  if (shape?.hasAttribute("width")) {
    return {
      x: numberIn(shape.getAttribute("x"), "x"),
      w: numberIn(shape.getAttribute("width"), "width"),
    };
  }
  throw new Error(
    `no horizontal geometry on ${element.outerHTML.slice(0, 120)}: expected style left/width in % or SVG x/width`,
  );
}

/** The linear time axis, fitted on the two long blocks whose starts are farthest apart. */
async function timeAxis(): Promise<{ at: (epoch: number) => number; scale: number; tol: number }> {
  const first = geometry(await blockOf(CANON));
  const last = geometry(await blockOf(TOUR));
  const scale = (last.x - first.x) / (TOUR.start - CANON.start);
  expect(scale).toBeGreaterThan(0);
  const span = BARBARE.end - CANON.start;
  return {
    at: (epoch) => first.x + scale * (epoch - CANON.start),
    scale,
    tol: 0.005 * scale * span,
  };
}

/** What a screen reader reads for the element: aria-label, aria-labelledby targets, text content. */
function spoken(element: Element): string {
  const labelledBy = (element.getAttribute("aria-labelledby") ?? "")
    .split(/\s+/)
    .filter(Boolean)
    .map((id) => textOf(document.getElementById(id)));
  return [element.getAttribute("aria-label") ?? "", ...labelledBy, textOf(element)].join(" ");
}

async function tooltipWith(name: string, timeout = 2_000): Promise<HTMLElement> {
  let match: HTMLElement | undefined;
  await waitFor(
    () => {
      match = screen.getAllByRole("tooltip").find((tip) => textOf(tip).includes(name));
      expect(match).toBeDefined();
    },
    { timeout },
  );
  return match as HTMLElement;
}

function rowOf(element: HTMLElement): HTMLElement {
  const row = element.closest<HTMLElement>("tr, [role='row']");
  if (row === null) {
    throw new Error(`"${element.textContent}" is not inside a table row`);
  }
  return row;
}

function sheetPath(href: string | null): string {
  return decodeURIComponent(new URL(href ?? "", window.location.href).pathname);
}

beforeEach(() => {
  localStorage.clear();
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW * 1000);
  installFetch(() => undefined);
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// -------------------------------------------------------------------------------- calendar page

describe("calendar page", () => {
  it("puts each end of the week in the column of its local day in the plan's zone", async () => {
    const formats = vi.spyOn(Intl, "DateTimeFormat");
    installFetch(withPlan);
    open("/calendrier");
    render(<App />);

    await shownDays(THIS_WEEK);
    await waitFor(() => expect(columnsOf(CANON.name)).toEqual(["2026-11-13"]));
    // 23:30 UTC on the 12th is 08:30 on the 13th in Tokyo.
    expect(columnsOf(MORTIER.name)).toEqual(["2026-11-11"]);
    expect(columnsOf(MUR.name)).toEqual(["2026-11-12"]);
    expect(columnsOf(TOUR.name)).toEqual(["2026-11-14"]);
    // Ends at 00:30 on Monday the 16th in Tokyo: not this week.
    expect(columnsOf(BARBARE.name)).toEqual([]);

    const thirteenth = textOf(column("2026-11-13"));
    expect(thirteenth).toMatch(clock(8, 30));
    expect(thirteenth).not.toMatch(clock(23, 30));
    expect(textOf(column("2026-11-14"))).toMatch(clock(1, 0));

    const zones = formats.mock.calls.map(
      ([, options]) => (options as { timeZone?: string } | undefined)?.timeZone,
    );
    expect(zones).toContain(ZONE);
  });

  it("changes the week with Semaine suivante and Semaine précédente, and comes back with Aujourd'hui", async () => {
    installFetch(withPlan);
    const user = userEvent.setup();
    open("/calendrier");
    render(<App />);

    await shownDays(THIS_WEEK);
    await waitFor(() => expect(columnsOf(CANON.name)).toEqual(["2026-11-13"]));

    await user.click(screen.getByRole("button", { name: "Semaine suivante" }));
    await shownDays(NEXT_WEEK);
    expect(columnsOf(BARBARE.name)).toEqual(["2026-11-16"]);
    expect(columnsOf(CANON.name)).toEqual([]);

    await user.click(screen.getByRole("button", { name: "Semaine précédente" }));
    await shownDays(THIS_WEEK);
    expect(columnsOf(CANON.name)).toEqual(["2026-11-13"]);

    await user.click(screen.getByRole("button", { name: "Semaine précédente" }));
    await shownDays(PREVIOUS_WEEK);
    for (const task of ASSIGNMENTS) {
      expect(columnsOf(task.name)).toEqual([]);
    }

    await user.click(screen.getByRole("button", { name: "Aujourd'hui" }));
    await shownDays(THIS_WEEK);
    expect(columnsOf(CANON.name)).toEqual(["2026-11-13"]);
    expect(columnsOf(TOUR.name)).toEqual(["2026-11-14"]);
  });
});

// ---------------------------------------------------------------------------------- Gantt page

describe("builders Gantt page", () => {
  it("draws one row per lane slot and one block per assignment carrying the API epochs", async () => {
    installFetch(withPlan);
    open("/constructeurs");
    render(<App />);

    await waitFor(() => expect(blocks()).toHaveLength(ASSIGNMENTS.length));
    const rows = Array.from(document.querySelectorAll<HTMLElement>("[data-lane][data-lane-slot]"));
    const pairs = rows.map((r) => `${r.dataset.lane}:${r.dataset.laneSlot}`);
    expect([...pairs].sort()).toEqual(["builder:0", "builder:1", "builder:2", "lab:0"]);

    for (const task of ASSIGNMENTS) {
      const block = await blockOf(task);
      expect(block.dataset.start).toBe(String(task.start));
      expect(block.dataset.end).toBe(String(task.end));
      const row = block.closest<HTMLElement>("[data-lane][data-lane-slot]");
      expect(row?.dataset.lane).toBe(task.lane);
      expect(row?.dataset.laneSlot).toBe(String(task.lane_slot));
    }
  });

  it("places blocks proportionally to time and keeps a very short block visible at its start", async () => {
    installFetch(withPlan);
    open("/constructeurs");
    render(<App />);

    const axis = await timeAxis();
    for (const task of ASSIGNMENTS) {
      const { x, w } = geometry(await blockOf(task));
      expect(Math.abs(x - axis.at(task.start))).toBeLessThanOrEqual(axis.tol);
      if (task !== MUR) {
        expect(Math.abs(w - axis.scale * (task.end - task.start))).toBeLessThanOrEqual(axis.tol);
      }
    }
    const short = geometry(await blockOf(MUR));
    expect(short.w).toBeGreaterThan(0);
    expect(short.w).toBeGreaterThanOrEqual(axis.scale * (MUR.end - MUR.start) - axis.tol);
  });

  it("marks frozen blocks apart from planned ones, with En cours or Prévu for screen readers", async () => {
    installFetch(withPlan);
    open("/constructeurs");
    render(<App />);

    const frozen = await blockOf(CANON);
    expect(frozen.dataset.status).toBe("frozen");
    expect(spoken(frozen)).toContain("En cours");
    expect(spoken(frozen)).not.toContain("Prévu");
    for (const task of [MORTIER, MUR, TOUR, BARBARE]) {
      const planned = await blockOf(task);
      expect(planned.dataset.status).toBe("planned");
      expect(spoken(planned)).toContain("Prévu");
      expect(spoken(planned)).not.toContain("En cours");
    }
  });

  it("draws the sleep and absence bands of the preview at their place on the time axis", async () => {
    installFetch(withPlan);
    open("/constructeurs");
    render(<App />);

    const axis = await timeAxis();
    await waitFor(() => expect(bands("sleep").length).toBeGreaterThanOrEqual(SLEEP.length));
    await waitFor(() => expect(bands("absence").length).toBeGreaterThanOrEqual(ABSENCE.length));
    for (const [kind, intervals] of [
      ["sleep", SLEEP],
      ["absence", ABSENCE],
    ] as const) {
      for (const [start, end] of intervals) {
        const band = bands(kind).find(
          (b) => b.dataset.start === String(start) && b.dataset.end === String(end),
        );
        expect(band, `${kind} band ${start}-${end}`).toBeDefined();
        const { x, w } = geometry(band as HTMLElement);
        expect(Math.abs(x - axis.at(start))).toBeLessThanOrEqual(axis.tol);
        expect(Math.abs(w - axis.scale * (end - start))).toBeLessThanOrEqual(axis.tol);
      }
    }
  });

  it("shows an equivalent table with the builder, the name, the local start and end", async () => {
    installFetch(withPlan);
    open("/constructeurs");
    render(<App />);

    const table = await screen.findByRole("table");
    for (const header of ["Constructeur", "Nom", "Début", "Fin"]) {
      expect(within(table).getByRole("columnheader", { name: header })).toBeTruthy();
    }
    for (const task of ASSIGNMENTS) {
      expect(within(table).getAllByText(task.name, { exact: false })).toHaveLength(1);
    }

    const canon = rowOf(within(table).getByText(CANON.name, { exact: false }));
    const canonText = textOf(canon);
    // The month is 11 too: only the end day (13 in Tokyo, 12 in UTC) is told apart.
    expect(canonText).toContain("Constructeur 1");
    expect(canonText).toMatch(clock(10, 0));
    expect(canonText).toMatch(/(?<![0-9])13(?![0-9])/);
    expect(canonText).toMatch(clock(8, 30));
    expect(canonText).not.toMatch(clock(23, 30));

    const tour = rowOf(within(table).getByText(TOUR.name, { exact: false }));
    expect(textOf(tour)).toContain("Constructeur 2");
    expect(textOf(tour)).toMatch(/(?<![0-9])14(?![0-9])/);
    expect(textOf(tour)).toMatch(clock(1, 0));

    const barbare = rowOf(within(table).getByText(BARBARE.name, { exact: false }));
    expect(textOf(barbare)).toContain("Laboratoire 1");
    expect(textOf(barbare)).toMatch(clock(0, 30));
  });

  it("lets the keyboard focus every block and shows its name and local dates on focus", async () => {
    installFetch(withPlan);
    open("/constructeurs");
    render(<App />);

    for (const task of ASSIGNMENTS) {
      const block = await blockOf(task);
      expect(block.tabIndex).toBeGreaterThanOrEqual(0);
      act(() => block.focus());
      expect(document.activeElement).toBe(block);
      await tooltipWith(task.name);
    }

    const canon = await blockOf(CANON);
    act(() => (document.activeElement as HTMLElement | null)?.blur());
    act(() => canon.focus());
    const tip = textOf(await tooltipWith(CANON.name));
    expect(tip).toMatch(clock(10, 0));
    expect(tip).toMatch(clock(8, 30));
    expect(tip).not.toMatch(clock(23, 30));
  });

  it("shows the name and local dates when a block is hovered", async () => {
    installFetch(withPlan);
    const user = userEvent.setup();
    open("/constructeurs");
    render(<App />);

    await user.hover(await blockOf(TOUR));
    const tip = textOf(await tooltipWith(TOUR.name));
    expect(tip).toMatch(clock(15, 1));
    expect(tip).toMatch(clock(1, 0));
  });
});

// ------------------------------------------------------------------------- no plan, task sheet

describe("without a plan", () => {
  for (const route of [
    "/calendrier",
    "/constructeurs",
    `/taches/${encodeURIComponent(TOUR.task_id)}`,
  ]) {
    it(`says Aucun plan with a link to Que faire maintenant ? on ${route}`, async () => {
      open(route);
      render(<App />);

      const main = screen.getByRole("main");
      await waitFor(() => expect(textOf(main)).toMatch(/Aucun plan/));
      const link = within(main).getByRole("link", { name: "Que faire maintenant ?" });
      expect(sheetPath(link.getAttribute("href"))).toBe("/maintenant");
    });
  }
});

describe("task sheet", () => {
  it("lists the prerequisites, each a link to its own sheet", async () => {
    installFetch(withPlan);
    const user = userEvent.setup();
    open(`/taches/${encodeURIComponent(TOUR.task_id)}`);
    render(<App />);

    expect(await screen.findByRole("heading", { name: TOUR.name })).toBeTruthy();
    const list = await screen.findByRole("list", { name: "Prérequis" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(TOUR.requires.length);

    for (const required of [MORTIER, MUR]) {
      const link = within(list).getByRole("link", { name: required.name });
      expect(sheetPath(link.getAttribute("href"))).toBe(`/taches/${required.task_id}`);
    }

    await user.click(within(list).getByRole("link", { name: MORTIER.name }));
    expect(await screen.findByRole("heading", { name: MORTIER.name })).toBeTruthy();
    await waitFor(() => expect(sheetPath(window.location.href)).toBe(`/taches/${MORTIER.task_id}`));
  });
});
