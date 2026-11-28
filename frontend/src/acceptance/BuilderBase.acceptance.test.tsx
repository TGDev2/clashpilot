/**
 * E33 acceptance tests: the village selector, the builder base in the interface and its README
 * section (frozen with the exam commit).
 *
 * Contract of the UI these tests drive (everything is French, labels come from `fr.ts`; the UI
 * computes no plan). The current village is `localStorage["clashpilot.village"]`; the tests set it,
 * render `App` at a route and drive the page. Texts are read on `textContent` with white space
 * collapsed, so sibling elements need real text nodes between them.
 *
 * Selector
 * - In the banner of `Layout` (its `header`), a native `<select>` labelled "Village affiché" with
 *   exactly two options, "Village principal" (the default) and "Base des ouvriers". Not radio
 *   buttons: a frozen test counts the radios of `/strategie`.
 * - The choice is kept in `localStorage` (the key is free). A read that throws gives "Village
 *   principal"; a write that throws keeps the choice for the session.
 * - Changing it on a page reads that page again for the chosen village.
 *
 * Requests of the dashboard, `/maintenant`, `/calendrier`, `/constructeurs`, `/historique`,
 * `/strategie` and `/hdv`
 * - With "Base des ouvriers": `?base=builder` on `GET .../plans/latest`, `recommendations/now`,
 *   `journal`, `critical-path` and on `POST .../plans/{plan_id}/events`; `"base": "builder"` in the
 *   body of `POST .../plans` and `POST .../town-hall/compare`. No other key changes.
 * - With "Village principal": no `base` parameter and no `base` key, as today.
 *
 * Labels with "Base des ouvriers" (inside `main`)
 * - The card of the dashboard reads "Hôtel des ouvriers {niveau} au plus tôt :" and `/hdv` is headed
 *   "Quand lancer l'Hôtel des ouvriers ?".
 * - The `lab` lane is the "Labo des étoiles" (card, `/hdv`, alerts, Gantt, checkboxes of
 *   `/strategie`). Neither "Hôtel de Ville" nor "laboratoire" is written on those pages.
 *
 * `/ressources`, `/scenarios` and `/objets` with "Base des ouvriers" say "Disponible pour le village
 * principal seulement" and send no request at all.
 *
 * Manual input (`#saisie` of `/importer`, whatever the selector)
 * - Fields "Or des ouvriers", "Élixir des ouvriers" (resources `builder_gold`, `builder_elixir`,
 *   status "confirmed" unless chosen) and "Ouvriers de la base" (`builder_base_builders`), filled
 *   with the saved values. A blank field sends no key.
 * - Each event has a native `<select>` labelled "Village de l'événement", "Village principal" by
 *   default (whatever the banner) or "Base des ouvriers"; the event is sent with `base: "builder"`
 *   for the second, and with `base: "home"` or no `base` for the first.
 *
 * `/strategie` lists, by name, the troops of the chosen village: the items of `GET .../state` whose
 * section is `units` (home) or `units2` (builder base).
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "../App";

// ---------------------------------------------------------------------------------- fetch stub

type Call = { method: string; path: string; search: string; body: string };
type Reply = { status?: number; body?: unknown };
type Handler = (call: Call) => Reply | undefined;

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
    const url = new URL(request.url);
    const call: Call = {
      method: request.method.toUpperCase(),
      path: url.pathname,
      search: url.search,
      body: await request.clone().text(),
    };
    calls.push(call);
    const reply = handler(call);
    if (reply === undefined) {
      return json(404, { detail: { code: "not_found", message: "Introuvable." } });
    }
    return json(reply.status ?? 200, reply.body);
  };
  vi.stubGlobal("fetch", (input: unknown, init?: unknown) => router.dispatch(input, init));
}

// -------------------------------------------------------------------------------------- fixtures

const TAG = "P2Y8QQ0";
const VILLAGE = `/api/villages/${TAG}`;
const LATEST = `${VILLAGE}/plans/latest`;
const PLANS = `${VILLAGE}/plans`;
const NOW_ACTIONS = `${VILLAGE}/recommendations/now`;
const JOURNAL = `${VILLAGE}/journal`;
const CRITICAL = `${VILLAGE}/critical-path`;
const COMPARE = `${VILLAGE}/town-hall/compare`;
const MANUAL = `${VILLAGE}/manual`;
const EVENTS = new RegExp(`^${VILLAGE}/plans/(\\d+)/events$`);

const NOW = Math.floor(Date.now() / 1000);
const DAY = 86_400;
const HOUR = 3_600;

type Base = "home" | "builder";

const PACK = { pack_id: "pack-alpha", game_version: "7.1-test", fictional: false };
const HEURISTIC = { kind: "heuristic", objective: 10, bound: null, gap_abs: null, scope: "" };
const PROFILE = {
  tz: "UTC",
  weekly: [],
  exceptions: [],
  policy: { mode: "strict", tolerance_minutes: 0, max_logins_per_day: null },
};

const PLAN_ID: Record<Base, number> = { home: 8, builder: 21 };
const TASKS: Record<Base, { builder: [string, number, string]; lab: [string, number, string] }> = {
  home: {
    builder: ["9300004:0:2", 9300004, "Canon de foire"],
    lab: ["9300005:0:2", 9300005, "Pique-lune"],
  },
  builder: {
    builder: ["9400004:0:2", 9400004, "Lance-bulles"],
    lab: ["9400006:0:2", 9400006, "Grognard de sable"],
  },
};

function assignments(base: Base) {
  return (["builder", "lab"] as const).map((lane) => {
    const [task_id, data_id, name] = TASKS[base][lane];
    return {
      task_id,
      data_id,
      name,
      lane,
      lane_slot: 0,
      start: NOW - HOUR,
      // Today: the calendar opens on the current week.
      end: NOW,
      requires: [],
      status: "planned",
    };
  });
}

function planOut(base: Base, overrides: Record<string, unknown> = {}) {
  return {
    plan_id: PLAN_ID[base],
    now: NOW,
    timezone: "UTC",
    horizon_days: 90,
    horizon_end: NOW + 90 * DAY,
    engine: "auto",
    profile: "fast",
    reproducible: false,
    input_hash: "i".repeat(64),
    score: 12.5,
    proof: HEURISTIC,
    pack: PACK,
    assignments: assignments(base),
    excluded: [],
    warnings: [],
    stale: false,
    strategy: null,
    composition: [],
    goal: null,
    war_windows: [],
    critical_path: false,
    town_hall_skip_lanes: base === "builder" ? ["lab"] : [],
    composition_first: false,
    ...overrides,
  };
}

function recommendations(base: Base) {
  const [task_id, , name] = TASKS[base].builder;
  return {
    plan_id: PLAN_ID[base],
    now: NOW,
    timezone: "UTC",
    fictional: false,
    pack: PACK,
    proof: HEURISTIC,
    actions: [
      {
        task_id,
        name,
        action: "start",
        at: NOW,
        end: NOW + HOUR,
        lane: "builder",
        lane_slot: 0,
        why: { reasons: [{ code: "best_ratio", message: "Meilleur rapport.", value: null }] },
        advantages: [],
        drawbacks: [],
        reliability: { level: "high", factors: [] },
        counterfactual: null,
        float_s: null,
        critical: null,
        date_gain_s: null,
      },
    ],
    critical_alerts:
      base === "builder"
        ? [{ code: "lane_stopped", lane: "lab", planned_start: NOW + HOUR, delay_s: HOUR }]
        : [],
  };
}

function estimate(value: number) {
  return { value, status: "estimated", source: "model", as_of: NOW };
}

const TARGET: Record<Base, { task_id: string; data_id: number; name: string; to_level: number }> = {
  home: { task_id: "9300001:0:12", data_id: 9300001, name: "Beffroi du port", to_level: 12 },
  builder: { task_id: "9400001:0:7", data_id: 9400001, name: "Maison des artisans", to_level: 7 },
};

function criticalPath(base: Base, skip: string[], overrides: Record<string, unknown> = {}) {
  return {
    target: TARGET[base],
    skip_lanes: skip,
    set_finish: estimate(NOW + 9 * DAY),
    finish_after: estimate(NOW + 13 * DAY),
    finish_parallel: estimate(NOW + 11 * DAY),
    bound: { kind: "work", lane: "lab" },
    chains: [],
    calendar_wait_s: null,
    critical_lanes: ["builder"],
    work_finish: [
      { lane: "builder", capacity: 2, work_s: 8 * DAY, finish: estimate(NOW + 8 * DAY) },
      { lane: "lab", capacity: 1, work_s: 9 * DAY, finish: estimate(NOW + 9 * DAY) },
    ],
    tasks: [],
    ...overrides,
  };
}

function comparison(base: Base) {
  return {
    target: TARGET[base],
    skip_lanes: [],
    options: [
      {
        kind: "after",
        status: "ok",
        reasons: [],
        th_start: NOW + 11 * DAY,
        th_finish: NOW + 15 * DAY,
        idle_builder_s: 2 * DAY,
        unfinished_count: 5,
        unfinished_by_lane: { builder: 3, lab: 2 },
        unfinished_work_s_by_lane: { builder: 6 * DAY, lab: 3 * DAY },
        unfinished_composition: { count: 1, work_s: DAY },
        heroes: [],
        engine: "auto",
        proof: HEURISTIC,
      },
    ],
    reference_plan_id: PLAN_ID[base],
    now: NOW,
    scope: `Plan n° ${PLAN_ID[base]}, horizon 90 j, moteur auto.`,
  };
}

function journal(base: Base) {
  return [
    {
      entry_id: PLAN_ID[base],
      type: "task_started",
      at: NOW - HOUR,
      task_id: TASKS[base].builder[0],
      payload: {},
      plan_id: PLAN_ID[base],
    },
  ];
}

function replanOut(base: Base, dryRun: boolean) {
  return {
    changes: [],
    consequences: { goal_finish_delta_s: null, idle_delta_s: 0, messages: [], score_delta: 0 },
    dry_run: dryRun,
    input_hash: "j".repeat(64),
    plan_id: dryRun ? null : PLAN_ID[base],
    previous_plan_id: PLAN_ID[base],
    proof: HEURISTIC,
  };
}

const PROVENANCE = { value: 1, status: "confirmed", source: "export", as_of: NOW };

function item(data_id: number, name: string, section: string, village: Base) {
  return { data_id, name, level: 1, count: 1, section, village, provenance: PROVENANCE };
}

const HOME_TROOPS = ["Pique-lune", "Voltigeur d'écume"];
const BUILDER_TROOPS = ["Grognard de sable", "Archère de verre"];
const HOME_OTHERS = ["Canon de foire", "Capitaine Brume"];
const BUILDER_OTHERS = ["Lance-bulles", "Gardien du crépuscule"];

const STATE = {
  village_id: TAG,
  snapshot_id: 1,
  tag: `#${TAG}`,
  exported_at: NOW - DAY,
  format_version: "2026-11",
  items: [
    item(9300004, "Canon de foire", "buildings", "home"),
    item(9300005, "Pique-lune", "units", "home"),
    item(9300006, "Voltigeur d'écume", "units", "home"),
    item(28000001, "Capitaine Brume", "heroes", "home"),
    item(9400004, "Lance-bulles", "buildings2", "builder"),
    item(9400006, "Grognard de sable", "units2", "builder"),
    item(9400008, "Archère de verre", "units2", "builder"),
    item(9400007, "Gardien du crépuscule", "heroes2", "builder"),
  ],
  running: [],
  unknown_ids: [],
  unknown_sections: [],
  warnings: [],
  resources: {},
  builders: PROVENANCE,
  builder_base_builders: PROVENANCE,
};

const STRATEGIES = {
  strategies: [
    { key: "balanced", label: "Équilibrée", weights: {} },
    { key: "defense", label: "Défense", weights: {} },
  ],
};

function parsed(call: Call): Record<string, unknown> {
  return (call.body === "" ? {} : JSON.parse(call.body)) as Record<string, unknown>;
}

/** A route whose answer depends on the village the request is about. */
function perBase(call: Call): boolean {
  if (call.method === "GET") {
    return [LATEST, NOW_ACTIONS, JOURNAL, CRITICAL].includes(call.path);
  }
  return (
    call.method === "POST" &&
    (call.path === PLANS || call.path === COMPARE || EVENTS.test(call.path))
  );
}

/** Whether the route takes the village in its body (else in its query). */
function baseInBody(call: Call): boolean {
  return call.method === "POST" && (call.path === PLANS || call.path === COMPARE);
}

/** The village a request asks for, where the API reads it; `undefined` when it names none. */
function baseOf(call: Call): unknown {
  return baseInBody(call)
    ? parsed(call).base
    : (new URLSearchParams(call.search).get("base") ?? undefined);
}

type World = {
  latest?: Partial<Record<Base, Reply>>;
  critical?: (base: Base, skip: string[]) => Reply;
  manual?: unknown;
};

const EMPTY_MANUAL = {
  village_id: TAG,
  resources: {},
  builders_total: null,
  builder_base_builders: null,
  magic_items: {},
  gold_pass: {},
  farm_rate: {},
  storage_override: {},
  events: [],
  source: "manual",
  updated_at: null,
};

function world({ latest, critical, manual = EMPTY_MANUAL }: World = {}): Handler {
  return (call) => {
    const base: Base = perBase(call) && baseOf(call) === "builder" ? "builder" : "home";
    if (call.method === "PUT" && call.path === MANUAL) {
      return { body: { ...EMPTY_MANUAL, ...parsed(call), updated_at: NOW } };
    }
    if (call.method === "POST") {
      if (call.path === PLANS) {
        return { status: 201, body: planOut(base) };
      }
      if (call.path === COMPARE) {
        return { body: comparison(base) };
      }
      if (call.path === `${VILLAGE}/schedule/to-epoch`) {
        return { body: { epoch: NOW - 60 } };
      }
      return EVENTS.test(call.path)
        ? { body: replanOut(base, parsed(call).dry_run === true) }
        : undefined;
    }
    switch (call.path) {
      case LATEST:
        return latest?.[base] ?? { body: planOut(base) };
      case NOW_ACTIONS:
        return { body: recommendations(base) };
      case JOURNAL:
        return { body: journal(base) };
      case CRITICAL: {
        const skip = new URLSearchParams(call.search).getAll("skip_lanes");
        return critical?.(base, skip) ?? { body: criticalPath(base, skip) };
      }
      case MANUAL:
        return { body: manual };
      case `${VILLAGE}/state`:
        return { body: STATE };
      case `${VILLAGE}/schedule`:
        return { body: PROFILE };
      case "/api/meta/strategies":
        return { body: STRATEGIES };
      case "/api/gamedata/packs":
        return { body: [] };
      default:
        return undefined;
    }
  };
}

// ----------------------------------------------------------------------------------- helpers

const SELECTOR = "Village affiché";
const HOME = "Village principal";
const BUILDER = "Base des ouvriers";
const MAIN_ONLY = "Disponible pour le village principal seulement";
const HOME_WORDS = /Hôtel de Ville|laboratoire/i;
const STAR_LAB = /labo des étoiles/i;

type User = ReturnType<typeof userEvent.setup>;

function open(route: string): void {
  localStorage.setItem("clashpilot.village", TAG);
  window.history.pushState({}, "", route);
}

/** The text of an element as it reads, every run of white space (non-breaking too) as one space. */
function textOf(node: Node): string {
  return (node.textContent ?? "").replace(/\s+/g, " ").trim();
}

function mainText(): string {
  return textOf(screen.getByRole("main"));
}

/** Lets the requests still in flight answer and the page render them. */
function pause(): Promise<void> {
  return new Promise((done) => setTimeout(done, 80));
}

/** The select of the banner: the `header` of `Layout`, the one outside `main`. */
function selector(): HTMLSelectElement {
  const banners = [...document.querySelectorAll("header")].filter((h) => !h.closest("main"));
  expect(banners, "banner of the layout").toHaveLength(1);
  return within(banners[0] as HTMLElement).getByRole("combobox", {
    name: SELECTOR,
  }) as HTMLSelectElement;
}

function selected(select: HTMLSelectElement): string {
  return textOf(select.options[select.selectedIndex] as HTMLOptionElement);
}

async function choose(user: User, village: typeof HOME | typeof BUILDER): Promise<void> {
  await user.selectOptions(selector(), village);
  expect(selected(selector())).toBe(village);
}

/** Chooses the builder base in an earlier visit, on a route that reads nothing. */
async function rememberBuilderBase(): Promise<void> {
  window.history.pushState({}, "", "/nowhere");
  const visit = render(<App />);
  await choose(userEvent.setup(), BUILDER);
  visit.unmount();
  calls = [];
}

function sent(method: string, path: string, from = 0): Call[] {
  return calls.slice(from).filter((c) => c.method === method && c.path === path);
}

/** Waits for a request of `method path` sent after the mark `from`, and returns the latest. */
async function request(method: string, path: string, from = 0): Promise<Call> {
  await waitFor(() => expect(sent(method, path, from), `${method} ${path}`).not.toHaveLength(0));
  return sent(method, path, from).at(-1) as Call;
}

/** The query of a request as sorted pairs: nothing more, nothing less. */
function query(call: Call): string[][] {
  return [...new URLSearchParams(call.search)].sort();
}

const BUILDER_QUERY = [["base", "builder"]];

/** Every per-village request sent after `from` is about the chosen village. */
function expectAllAbout(base: Base, from: number): void {
  const concerned = calls.slice(from).filter(perBase);
  expect(concerned.length).toBeGreaterThan(0);
  for (const call of concerned) {
    expect(baseOf(call), `${call.method} ${call.path}${call.search} ${call.body}`).toBe(
      base === "builder" ? "builder" : undefined,
    );
  }
}

/**
 * Opens `route` on the home village, switches to the builder base and back. At each step the GETs of
 * `paths` are sent again for the chosen village and `shows` checks what the page reads; the last of
 * those GETs is then read, so that a request overtaken while the page settles does not count.
 */
async function switchBothWays(
  route: string,
  paths: string[],
  shows: (base: Base) => void,
): Promise<void> {
  installFetch(world());
  open(route);
  render(<App />);
  const user = userEvent.setup();

  const settled = async (base: Base, from: number) => {
    for (const path of paths) {
      await request("GET", path, from);
    }
    await waitFor(() => shows(base));
    await pause();
    for (const path of paths) {
      const last = sent("GET", path, from).at(-1) as Call;
      if (base === "home") {
        expect(last.search, path).toBe("");
      } else {
        expect(query(last).filter(([key]) => key === "base")).toEqual(BUILDER_QUERY);
      }
    }
  };
  await settled("home", 0);

  const toBuilder = calls.length;
  await choose(user, BUILDER);
  await settled("builder", toBuilder);
  expectAllAbout("builder", toBuilder);

  const back = calls.length;
  await choose(user, HOME);
  await settled("home", back);
  expectAllAbout("home", back);
}

/** The main text names the tasks of `base` and none of the other village. */
function expectTasksOf(base: Base, text: () => string = mainText): void {
  const other: Base = base === "home" ? "builder" : "home";
  expect(text()).toContain(TASKS[base].builder[2]);
  expect(text()).not.toContain(TASKS[other].builder[2]);
}

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("lang");
  window.history.pushState({}, "", "/nowhere");
  installFetch(() => undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// ------------------------------------------------------------------------------------ selector

describe("village selector of the banner", () => {
  it("offers the two villages, the home village first chosen", () => {
    installFetch(world());
    render(<App />);

    const select = selector();
    expect(select.tagName).toBe("SELECT");
    expect([...select.options].map(textOf)).toEqual([HOME, BUILDER]);
    expect(selected(select)).toBe(HOME);
  });

  it("remembers the choice, both ways, for the next visit", async () => {
    installFetch(world());
    await rememberBuilderBase();

    const second = render(<App />);
    expect(selected(selector())).toBe(BUILDER);
    await choose(userEvent.setup(), HOME);
    second.unmount();

    render(<App />);
    expect(selected(selector())).toBe(HOME);
  });

  it("falls back to the home village when the stored choice cannot be read", async () => {
    installFetch(world());
    await rememberBuilderBase();
    vi.spyOn(Storage.prototype, "getItem").mockImplementation((key: string) => {
      if (key === "clashpilot.village") {
        return TAG;
      }
      throw new Error("storage denied");
    });
    expect(() => localStorage.getItem("anything else")).toThrow("storage denied");
    window.history.pushState({}, "", "/historique");
    render(<App />);

    expect(selected(selector())).toBe(HOME);
    expect((await request("GET", JOURNAL)).search).toBe("");
  });

  it("keeps the choice for the session when it cannot be stored", async () => {
    installFetch(world());
    open("/historique");
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("quota exceeded");
    });
    expect(() => localStorage.setItem("any", "value")).toThrow("quota exceeded");
    render(<App />);
    await request("GET", JOURNAL);

    const mark = calls.length;
    await choose(userEvent.setup(), BUILDER);
    expect(query(await request("GET", JOURNAL, mark))).toEqual(BUILDER_QUERY);
  });
});

// ------------------------------------------------------------------------------------ requests

describe("pages of the chosen village", () => {
  it("Tableau de bord reads the plan and the critical path of the chosen village", async () => {
    await switchBothWays("/", [LATEST, CRITICAL], (base) => {
      const card = document.querySelector<HTMLElement>("[data-critical-path]");
      expect(card).not.toBeNull();
      expect(textOf(card as HTMLElement)).toContain(
        base === "home" ? "Hôtel de Ville 12 au plus tôt :" : "Hôtel des ouvriers 7 au plus tôt :",
      );
    });
    // The lanes the builder plan skips go with its village, and the home ones are left as before.
    const builderPaths = sent("GET", CRITICAL).filter((c) => baseOf(c) === "builder");
    expect(query(builderPaths.at(-1) as Call)).toEqual([
      ["base", "builder"],
      ["skip_lanes", "lab"],
    ]);
    expect(sent("GET", CRITICAL).at(-1)?.search).toBe("");
  });

  it.each([
    [HOME, "home"],
    [BUILDER, "builder"],
  ] as const)("Tableau de bord replans the newer import of « %s »", async (_name, base) => {
    installFetch(world({ latest: { [base]: { body: planOut(base, { stale: true }) } } }));
    if (base === "builder") {
      await rememberBuilderBase();
    }
    open("/");
    render(<App />);
    if (base === "home") {
      expect(selected(selector())).toBe(HOME);
    }

    await userEvent
      .setup()
      .click(await screen.findByRole("button", { name: "Recalculer le plan" }));
    const event = await request("POST", `${PLANS}/${PLAN_ID[base]}/events`);
    expect(query(event)).toEqual(base === "builder" ? BUILDER_QUERY : []);
    expect(parsed(event)).toEqual({ type: "snapshot_imported", dry_run: false });
  });

  it("Que faire maintenant ? reads the actions of the chosen village", async () => {
    await switchBothWays("/maintenant", [NOW_ACTIONS, LATEST], (base) => expectTasksOf(base));
  });

  it("Que faire maintenant ? computes the plan of the chosen village", async () => {
    installFetch(world());
    open("/maintenant");
    render(<App />);
    const user = userEvent.setup();
    const button = await screen.findByRole("button", { name: "Calculer mon plan" });

    await user.click(button);
    const home = await request("POST", PLANS);
    expect(parsed(home)).toEqual({});
    expect(home.search).toBe("");

    await choose(user, BUILDER);
    const mark = calls.length;
    await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
    await user.click(screen.getByRole("button", { name: "Calculer mon plan" }));
    const builder = await request("POST", PLANS, mark);
    expect(parsed(builder)).toEqual({ base: "builder" });
    expect(builder.search).toBe("");
  });

  it.each([
    [HOME, "home"],
    [BUILDER, "builder"],
  ] as const)("Que faire maintenant ? records the launches of « %s »", async (_name, base) => {
    installFetch(world());
    if (base === "builder") {
      await rememberBuilderBase();
    }
    open("/maintenant");
    render(<App />);
    const user = userEvent.setup();
    if (base === "home") {
      expect(selected(selector())).toBe(HOME);
    }
    const events = `${PLANS}/${PLAN_ID[base]}/events`;
    const expected = base === "builder" ? BUILDER_QUERY : [];
    const taskId = TASKS[base].builder[0];

    await user.click(await screen.findByRole("button", { name: "Simuler un retard" }));
    await user.click(screen.getByRole("button", { name: "15 min" }));
    const dryRun = await request("POST", events);
    expect(query(dryRun)).toEqual(expected);
    expect(parsed(dryRun)).toEqual({
      type: "task_started",
      task_id: taskId,
      at: NOW + 900,
      dry_run: true,
    });

    let mark = calls.length;
    await user.click(await screen.findByRole("button", { name: "Confirmer" }));
    const confirmed = await request("POST", events, mark);
    expect(query(confirmed)).toEqual(expected);
    expect(parsed(confirmed).dry_run).toBe(false);
    await pause();

    mark = calls.length;
    await user.click(await screen.findByRole("button", { name: "Je l'ai lancée" }));
    await user.click(await screen.findByRole("button", { name: "Valider" }));
    const launched = await request("POST", events, mark);
    expect(query(launched)).toEqual(expected);
    expect(parsed(launched)).toEqual({
      type: "task_started",
      task_id: taskId,
      at: NOW - 60,
      dry_run: false,
    });
  });

  it("Calendrier reads the plan of the chosen village", async () => {
    await switchBothWays("/calendrier", [LATEST], (base) => expectTasksOf(base));
  });

  it("Constructeurs reads the plan of the chosen village", async () => {
    await switchBothWays("/constructeurs", [LATEST], (base) => expectTasksOf(base));
  });

  it("Historique reads the journal of the chosen village", async () => {
    await switchBothWays("/historique", [JOURNAL], (base) => {
      const other: Base = base === "home" ? "builder" : "home";
      expect(mainText()).toContain(TASKS[base].builder[0]);
      expect(mainText()).not.toContain(TASKS[other].builder[0]);
    });
  });

  it("Stratégie computes the plan of the chosen village", async () => {
    installFetch(world());
    open("/strategie");
    render(<App />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole("radio", { name: "Défense" }));
    await user.type(screen.getByLabelText("Composition"), "9300005");
    await user.click(screen.getByRole("button", { name: "Calculer mon plan" }));
    const home = await request("POST", PLANS);
    expect(parsed(home)).toEqual({ strategy: "defense", composition: [9300005] });
    expect(home.search).toBe("");
    await pause();

    await choose(user, BUILDER);
    await user.click(await screen.findByRole("radio", { name: "Défense" }));
    const composition = screen.getByLabelText("Composition");
    await user.clear(composition);
    await user.type(composition, "9400006");
    const mark = calls.length;
    await user.click(screen.getByRole("button", { name: "Calculer mon plan" }));
    const builder = await request("POST", PLANS, mark);
    expect(parsed(builder)).toEqual({
      strategy: "defense",
      composition: [9400006],
      base: "builder",
    });
    expect(builder.search).toBe("");
  });

  it("/hdv compares the launches of the chosen village", async () => {
    installFetch(world());
    open("/hdv");
    render(<App />);
    const user = userEvent.setup();

    expect((await request("GET", LATEST)).search).toBe("");
    await user.click(await screen.findByRole("button", { name: "Comparer" }));
    const home = await request("POST", COMPARE);
    expect(parsed(home)).toEqual({ skip_lanes: [] });
    expect(home.search).toBe("");
    await pause();

    let mark = calls.length;
    await choose(user, BUILDER);
    expect(query(await request("GET", LATEST, mark))).toEqual(BUILDER_QUERY);
    // The box follows the lanes the builder plan skips.
    await waitFor(() =>
      expect(
        (screen.getByRole("checkbox", { name: /n'attend pas le/i }) as HTMLInputElement).checked,
      ).toBe(true),
    );
    // The options of the home village are not shown as those of the builder base.
    expect(document.querySelectorAll("[data-option]")).toHaveLength(0);
    mark = calls.length;
    await user.click(screen.getByRole("button", { name: "Comparer" }));
    const builder = await request("POST", COMPARE, mark);
    expect(parsed(builder)).toEqual({ skip_lanes: ["lab"], base: "builder" });
    expect(builder.search).toBe("");
  });

  it.each([
    ["/", [LATEST, CRITICAL]],
    ["/maintenant", [NOW_ACTIONS, LATEST]],
    ["/calendrier", [LATEST]],
    ["/constructeurs", [LATEST]],
    ["/historique", [JOURNAL]],
    ["/hdv", [LATEST]],
  ])("%s asks only for the builder base once it was chosen", async (route, paths) => {
    installFetch(world());
    await rememberBuilderBase();
    open(route);
    render(<App />);

    for (const path of paths) {
      await request("GET", path);
    }
    await pause();
    expectAllAbout("builder", 0);
  });
});

// -------------------------------------------------------------------------------------- labels

describe("labels of the builder base", () => {
  it("names the Hôtel des ouvriers and the Labo des étoiles on the dashboard", async () => {
    installFetch(world());
    await rememberBuilderBase();
    open("/");
    render(<App />);

    await waitFor(() => expect(mainText()).toContain("Hôtel des ouvriers 7 au plus tôt :"));
    expect(textOf(document.querySelector('[data-lane-work="lab"]') as Element)).toContain(
      "Labo des étoiles",
    );
    expect(mainText()).toMatch(/Fixée par\s?: .*labo des étoiles/i);
    expect(mainText()).toMatch(/labo des étoiles n'est pas attendu/i);
    expect(mainText()).not.toMatch(HOME_WORDS);
  });

  it("says there is no Hôtel des ouvriers to upgrade", async () => {
    installFetch(
      world({
        critical: (base) => ({
          body: criticalPath(base, [], {
            target: null,
            bound: null,
            work_finish: [],
            critical_lanes: [],
          }),
        }),
      }),
    );
    await rememberBuilderBase();
    open("/");
    render(<App />);

    await waitFor(() => expect(mainText()).toMatch(/Aucun Hôtel des ouvriers/));
    expect(mainText()).not.toMatch(HOME_WORDS);
  });

  it("names them on /hdv, before and after the comparison", async () => {
    installFetch(world());
    await rememberBuilderBase();
    open("/hdv");
    render(<App />);

    expect(
      await screen.findByRole("heading", { name: /^Quand lancer l'Hôtel des ouvriers\s\?$/ }),
    ).toBeDefined();
    await screen.findByRole("checkbox", { name: /n'attend pas le labo des étoiles/i });
    expect(mainText()).not.toMatch(HOME_WORDS);
    await userEvent.setup().click(screen.getByRole("button", { name: "Comparer" }));
    await waitFor(() => expect(document.querySelector('[data-lane="lab"]')).not.toBeNull());
    expect(textOf(document.querySelector('[data-lane="lab"]') as Element)).toMatch(STAR_LAB);
    expect(mainText()).toContain("Hôtel des ouvriers 7");
    expect(mainText()).not.toMatch(HOME_WORDS);
  });

  it("names them in the idle alert of Que faire maintenant ?", async () => {
    installFetch(world());
    await rememberBuilderBase();
    open("/maintenant");
    render(<App />);

    await waitFor(() => {
      const alert = screen.queryAllByRole("alert").find((a) => textOf(a).includes("arrêt"));
      expect(alert, "idle alert").toBeDefined();
      expect(textOf(alert as HTMLElement)).toMatch(STAR_LAB);
      expect(textOf(alert as HTMLElement)).toContain("Hôtel des ouvriers");
    });
    expect(mainText()).not.toMatch(HOME_WORDS);
  });

  it("names the lab lane Labo des étoiles in the Gantt", async () => {
    installFetch(world());
    await rememberBuilderBase();
    open("/constructeurs");
    render(<App />);

    await waitFor(() => expect(mainText()).toContain("Labo des étoiles 1"));
    expect(mainText()).not.toMatch(HOME_WORDS);
  });

  it("names them on Stratégie", async () => {
    installFetch(world());
    await rememberBuilderBase();
    open("/strategie");
    render(<App />);

    await screen.findByRole("radio", { name: "Défense" });
    expect(
      screen.getByRole("checkbox", { name: /n'attend pas le labo des étoiles/i }),
    ).toBeDefined();
    expect(screen.getByRole("checkbox", { name: /labo des étoiles.*composition/i })).toBeDefined();
    expect(mainText()).toContain("Hôtel des ouvriers");
    expect(mainText()).not.toMatch(HOME_WORDS);
  });
});

// ------------------------------------------------------------------- pages of the home village

describe.each([
  ["/ressources", /^Ressources$/],
  ["/scenarios", /^Scénarios$/],
  ["/objets", /^Objets magiques$/],
])("%s, for the home village only", (route, title) => {
  it("says so on the builder base and sends nothing", async () => {
    installFetch(world());
    await rememberBuilderBase();
    open(route);
    render(<App />);

    await waitFor(() => expect(mainText()).toContain(MAIN_ONLY));
    expect(screen.getAllByRole("heading", { name: title }).length).toBeGreaterThan(0);
    const user = userEvent.setup();
    for (const button of within(screen.getByRole("main")).queryAllByRole("button")) {
      await user.click(button);
    }
    await pause();
    expect(calls).toEqual([]);
  });

  it("stops asking when the builder base is chosen and comes back with the home village", async () => {
    installFetch(world());
    open(route);
    render(<App />);
    const user = userEvent.setup();
    await pause();
    expect(mainText()).not.toContain(MAIN_ONLY);

    const mark = calls.length;
    await choose(user, BUILDER);
    await waitFor(() => expect(mainText()).toContain(MAIN_ONLY));
    await pause();
    expect(calls.slice(mark)).toEqual([]);

    await choose(user, HOME);
    await waitFor(() => expect(mainText()).not.toContain(MAIN_ONLY));
    expect(screen.getAllByRole("heading", { name: title }).length).toBeGreaterThan(0);
  });
});

// -------------------------------------------------------------------------------- manual input

function saisie(): HTMLElement {
  const section = document.getElementById("saisie");
  expect(section, "element with id saisie").not.toBeNull();
  return section as HTMLElement;
}

function field(label: string): HTMLInputElement {
  return within(saisie()).getByLabelText(label) as HTMLInputElement;
}

async function openManual(manual?: unknown): Promise<User> {
  installFetch(world({ manual }));
  open("/importer");
  render(<App />);
  await waitFor(() => expect(field("Or")).toBeDefined());
  return userEvent.setup();
}

async function save(user: User): Promise<Record<string, unknown>> {
  await user.click(within(saisie()).getByRole("button", { name: "Enregistrer la saisie" }));
  return parsed(await request("PUT", MANUAL));
}

describe("manual input of the builder base", () => {
  it.each([HOME, BUILDER] as const)(
    "sends the resources and builders typed with « %s » shown",
    async (village) => {
      const user = await openManual();
      await choose(user, village);
      await user.type(await within(saisie()).findByLabelText("Or des ouvriers"), "700");
      await user.type(field("Élixir des ouvriers"), "800");
      await user.type(field("Ouvriers de la base"), "3");

      const body = await save(user);
      expect(body.resources).toMatchObject({
        builder_gold: { value: 700, status: "confirmed" },
        builder_elixir: { value: 800, status: "confirmed" },
      });
      expect(Object.keys(body.resources as object).sort()).toEqual([
        "builder_elixir",
        "builder_gold",
      ]);
      expect(body.builder_base_builders).toBe(3);
    },
  );

  it("sends no key for a builder base field left blank", async () => {
    const user = await openManual();
    expect(field("Or des ouvriers").value).toBe("");
    expect(field("Élixir des ouvriers").value).toBe("");
    expect(field("Ouvriers de la base").value).toBe("");
    await user.type(field("Or"), "5000");

    const body = await save(user);
    expect(Object.keys(body.resources as object)).toEqual(["gold"]);
    expect(body).not.toHaveProperty("builder_base_builders");
  });

  it("shows the saved values and no longer sends the ones cleared", async () => {
    const typed = (value: number) => ({ value, status: "confirmed", as_of: NOW, source: "manual" });
    const user = await openManual({
      ...EMPTY_MANUAL,
      resources: { builder_gold: typed(700), builder_elixir: typed(800) },
      builder_base_builders: 3,
      updated_at: NOW,
    });
    await waitFor(() => expect(field("Or des ouvriers").value).toBe("700"));
    expect(field("Élixir des ouvriers").value).toBe("800");
    expect(field("Ouvriers de la base").value).toBe("3");
    await user.clear(field("Or des ouvriers"));
    await user.clear(field("Ouvriers de la base"));

    const body = await save(user);
    expect(Object.keys(body.resources as object)).toEqual(["builder_elixir"]);
    expect(body.resources).toMatchObject({ builder_elixir: { value: 800 } });
    expect(body).not.toHaveProperty("builder_base_builders");
  });
});

describe("village of a game event", () => {
  function villages(): HTMLSelectElement[] {
    return within(saisie()).getAllByLabelText("Village de l'événement") as HTMLSelectElement[];
  }

  async function addEvent(user: User, index: number, name: string): Promise<void> {
    await user.click(within(saisie()).getByRole("button", { name: "Ajouter un événement" }));
    const nth = (label: string) => within(saisie()).getAllByLabelText(label)[index] as HTMLElement;
    // Set at once: typing every key would bring the test close to its time limit.
    const set = (label: string, value: string) =>
      fireEvent.change(nth(label), { target: { value } });
    set("Nom de l'événement", name);
    set("Début", "2027-01-10T08:00");
    set("Fin", "2027-01-17T08:00");
  }

  function sentVillages(body: Record<string, unknown>): string[][] {
    return (body.events as Array<{ label: string; base?: string }>).map((event) => [
      event.label,
      event.base ?? "home",
    ]);
  }

  it.each([HOME, BUILDER] as const)(
    "is the home village by default with « %s » shown, and sent as chosen",
    async (village) => {
      const user = await openManual();
      await choose(user, village);
      await addEvent(user, 0, "Chez nous");
      await addEvent(user, 1, "Chez les artisans");
      for (const select of villages()) {
        expect(select.tagName).toBe("SELECT");
        expect([...select.options].map(textOf)).toEqual([HOME, BUILDER]);
        expect(selected(select)).toBe(HOME);
      }
      await user.selectOptions(villages()[1] as HTMLSelectElement, BUILDER);

      const body = await save(user);
      expect(sentVillages(body)).toEqual([
        ["Chez nous", "home"],
        ["Chez les artisans", "builder"],
      ]);
    },
  );

  it("shows the village of a saved event and sends the new one", async () => {
    const saved = (label: string, base: Base) => ({
      label,
      lane: "builder",
      start: NOW,
      end: NOW + DAY,
      duration_pct: 80,
      cost_pct: 100,
      base,
    });
    const user = await openManual({
      ...EMPTY_MANUAL,
      events: [saved("Premier", "builder"), saved("Second", "builder")],
      updated_at: NOW,
    });
    await waitFor(() => expect(villages().map(selected)).toEqual([BUILDER, BUILDER]));
    await user.selectOptions(villages()[1] as HTMLSelectElement, HOME);

    const body = await save(user);
    expect(sentVillages(body)).toEqual([
      ["Premier", "builder"],
      ["Second", "home"],
    ]);
  });
});

// ------------------------------------------------------------------------------------- troops

describe("Stratégie: troops of the chosen village", () => {
  it("lists them for the composition and follows the selector", async () => {
    installFetch(world());
    open("/strategie");
    render(<App />);
    const user = userEvent.setup();

    const lists = async (listed: string[], absent: string[]) => {
      await waitFor(() => {
        for (const name of listed) {
          expect(mainText()).toContain(name);
        }
        for (const name of absent) {
          expect(mainText()).not.toContain(name);
        }
      });
    };
    await lists(HOME_TROOPS, [...BUILDER_TROOPS, ...HOME_OTHERS, ...BUILDER_OTHERS]);
    await choose(user, BUILDER);
    await lists(BUILDER_TROOPS, [...HOME_TROOPS, ...HOME_OTHERS, ...BUILDER_OTHERS]);
    await choose(user, HOME);
    await lists(HOME_TROOPS, [...BUILDER_TROOPS, ...HOME_OTHERS, ...BUILDER_OTHERS]);
  });
});

// ------------------------------------------------------------------------------------- README

describe("README: builder base", () => {
  /** The body of the section, up to the next heading of the same level or above. */
  function section(): string {
    // Vitest runs from `frontend/`.
    const readme = readFileSync(resolve(process.cwd(), "..", "README.md"), "utf8").replace(
      /\r\n/g,
      "\n",
    );
    const heading = /^(#{2,3}) +Base des ouvriers *$/m.exec(readme);
    expect(heading, "section « Base des ouvriers »").not.toBeNull();
    const rest = readme.slice((heading?.index ?? 0) + (heading?.[0].length ?? 0));
    const next = new RegExp(`^#{1,${heading?.[1]?.length ?? 2}} `, "m").exec(rest);
    // Wrapped lines read as one text.
    return rest.slice(0, next?.index ?? rest.length).replace(/\s+/g, " ");
  }

  it.each([
    ["rebuilding the pack", /reconstru/i],
    ["the pack itself", /\bpack\b/i],
    ["the builder count", /Ouvriers de la base|nombre d['’]ouvriers/i],
    ["the builder gold", /\bor des ouvriers/i],
    ["the builder elixir", /élixir des ouvriers/i],
    ["choosing the village", /Village principal|Village affiché/i],
    ["the clock tower", /Tour de l['’]horloge/i],
    ["the helpers", /\baides\b/i],
    ["what is not modelled", /(non|pas) (sont )?modélisée?s?|ne (sont|modélise) pas/i],
    ["B.O.B", /B\.O\.B/],
  ])("explains %s", (_name, pattern) => {
    expect(section()).toMatch(pattern);
  });

  it("states that B.O.B is not linked to the home village", () => {
    expect(section()).toMatch(/B\.O\.B[^#]*village principal|village principal[^#]*B\.O\.B/i);
  });
});
