/**
 * E27 acceptance tests: the earliest town hall date on the dashboard, the critical badges and alerts of
 * "Que faire maintenant ?" and the three critical path options of "Stratégie" (frozen with the exam
 * commit).
 *
 * Contract of the UI these tests drive (everything is French, labels come from `fr.ts`; the UI computes
 * no plan and no date: it shows what the API answers). The current village is
 * `localStorage["clashpilot.village"]`; the tests set it, render `App` at the route and drive the page.
 * Texts are read on `textContent` with white space collapsed, so sibling elements need real text nodes
 * between them. Durations are those of `formatDuration` ("20 j 3 h 30 min").
 *
 * Tableau de bord (`/`). One element `[data-critical-path]` fed by
 * `GET /api/villages/{id}/critical-path`, called with one `skip_lanes` query parameter per lane of
 * `town_hall_skip_lanes` of `GET .../plans/latest` (none without such a lane or without a plan: the card
 * shows without a plan too). Epochs are shown with `formatInZone` in the zone `tz` of
 * `GET .../schedule` (not the zone of the plan). Everything below is inside `[data-critical-path]`:
 * - `[data-date="parallel"]` holds "Hôtel de Ville {to_level} au plus tôt : {date}" (`finish_parallel`)
 *   and `[data-date="after"]` holds "Si l'HDV attend tout le reste : {date}" (`finish_after`); the card
 *   carries the badge "Estimé".
 * - `bound.kind` "chain": "Fixée par : {names}", the names of `chains` in the API order, separated by a
 *   comma, "et", "·", ";", "/" or "+", outside the `[data-chain]` and `[data-lane-work]` elements.
 * - `bound.kind` "work": "Fixée par : {lane} ({duration})" with the lane `bound.lane` named "les
 *   ouvriers" (builder), "le laboratoire" (lab), "la forge" (blacksmith) or "la maison des animaux"
 *   (pet_house), and the duration of `work_s` of that lane in `work_finish` (the remaining work, not
 *   the time left until `finish`).
 * - One `[data-chain]` per chain, in the API order: "{name} : niveau {from_level} → {to_level}".
 * - One `[data-lane-work]` per entry of `work_finish`, naming its lane and holding the date of its
 *   `finish`; those of `critical_lanes`, and only those, say "Ne doit jamais s'arrêter".
 * - `calendar_wait_s` above 0: "Le sommeil retarde cette date de {duration}"; nothing for 0 or null.
 * - `target` null: "Aucun Hôtel de Ville à améliorer", no date.
 * - "Le laboratoire n'est pas attendu" when `town_hall_skip_lanes` of the latest plan holds "lab".
 * A 409 `missing_input` of the route is a `ProblemNotice`: the API message in a `role="alert"` with the
 * link `/importer#saisie` (this alert may sit anywhere on the page).
 *
 * Que faire maintenant ? (`/maintenant`). In the row (`article`...) of each `start` action: "Critique"
 * when `critical` is true, else "Marge {duration of float_s}" when `float_s` is not null; "Avance ta date
 * de {duration}" when `date_gain_s` is above 0; the badge "Ta compo" when the data id of the action (the
 * first part of its `task_id` "{data_id}:{instance}:{to_level}", also given by the assignments of the
 * latest plan) is in `composition` of `GET .../plans/latest`. A `wait` action has none of these. Each
 * entry of `critical_alerts` is its own `role="alert"`: "{name} est à l'arrêt : chaque heure d'arrêt
 * retarde ton Hôtel de Ville" (`chain_stopped`) or the same sentence after the lane name above
 * (`lane_stopped`, first letter in either case; "les ouvriers sont" is accepted too), with
 * "({duration} prévues)" when `delay_s` is not null and no "prévues" otherwise.
 *
 * Stratégie (`/strategie`). Three checkboxes, unchecked at first, named exactly "Viser la date d'HDV au
 * plus tôt" (`critical_path: true`), "L'Hôtel de Ville n'attend pas le laboratoire"
 * (`town_hall_skip_lanes: ["lab"]`) and "Laboratoire : ma composition d'abord"
 * (`composition_first: true`; disabled while the composition is blank, with the text "Choisis d'abord tes
 * troupes et tes sorts" outside its name). An unchecked or disabled box sends no key at all: the body of
 * `POST .../plans` is then the one of E20.
 */
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

function callsTo(method: string, path: string): Call[] {
  return calls.filter((c) => c.method === method && c.path === path);
}

// -------------------------------------------------------------------------------------- fixtures

const TAG = "P2Y8QQ0";
const VILLAGE = `/api/villages/${TAG}`;
const CRITICAL = `${VILLAGE}/critical-path`;
const LATEST = `${VILLAGE}/plans/latest`;
const PLANS = `${VILLAGE}/plans`;
const RECO = `${VILLAGE}/recommendations/now`;
const ZONE = "Asia/Kolkata"; // UTC+5:30, no daylight saving time
const PLAN_ZONE = "America/Lima"; // the dashboard card must not use it

function epoch(year: number, month: number, day: number, hour: number, minute: number): number {
  return Date.UTC(year, month - 1, day, hour, minute) / 1000;
}

const NOW = epoch(2026, 11, 12, 12, 0);
// Each pair: an epoch and the way `formatInZone` writes it in ZONE.
const PARALLEL = epoch(2026, 12, 19, 22, 45);
const PARALLEL_TEXT = "20/12/2026 04:15";
const PARALLEL_ALL = epoch(2027, 2, 14, 22, 45); // the answer when no lane is skipped
const PARALLEL_ALL_TEXT = "15/02/2027 04:15";
const AFTER = epoch(2027, 1, 3, 13, 30);
const AFTER_TEXT = "03/01/2027 19:00";
const BUILDER_END = epoch(2026, 11, 25, 3, 30);
const BUILDER_END_TEXT = "25/11/2026 09:00";
const LAB_END = epoch(2026, 12, 10, 12, 0);
const LAB_END_TEXT = "10/12/2026 17:30";
const SMITH_END = epoch(2026, 11, 18, 14, 45);
const SMITH_END_TEXT = "18/11/2026 20:15";
const PET_END = epoch(2026, 11, 21, 6, 30);

const WORK_S = 20 * 86_400 + 3 * 3_600 + 30 * 60;
const WORK_TEXT = "20 j 3 h 30 min";

const HEURISTIC = { kind: "heuristic", objective: 10, bound: null, gap_abs: null, scope: "" };
const PACK = { pack_id: "pack-alpha", game_version: "7.1-test", fictional: false };

const PROFILE = {
  tz: ZONE,
  weekly: [],
  exceptions: [],
  policy: { mode: "strict", tolerance_minutes: 0, max_logins_per_day: null },
};

const estimated = (value: number) => ({ value, status: "estimated", source: "model", as_of: NOW });
const UNKNOWN = { value: null, status: "unknown", source: "model", as_of: NOW };

function lane(name: string, finish: number, capacity = 1, work = finish - NOW) {
  return { lane: name, capacity, work_s: work, finish: estimated(finish) };
}

const CHAINS = [
  {
    data_id: 28000001,
    name: "Roi d'essai",
    from_level: 3,
    to_level: 7,
    task_ids: ["28000001:0:4"],
    finish: estimated(LAB_END),
  },
  {
    data_id: 28000002,
    name: "Reine d'essai",
    from_level: 10,
    to_level: 12,
    task_ids: ["28000002:0:11"],
    finish: estimated(BUILDER_END),
  },
];

function criticalPath(overrides: Record<string, unknown> = {}) {
  return {
    target: {
      task_id: "1000001:0:12",
      data_id: 1000001,
      name: "Hôtel de Ville",
      to_level: 12,
    },
    set_finish: estimated(LAB_END),
    finish_after: estimated(AFTER),
    finish_parallel: estimated(PARALLEL),
    work_finish: [
      lane("builder", BUILDER_END, 5, WORK_S),
      lane("lab", LAB_END),
      lane("blacksmith", SMITH_END),
    ],
    bound: { kind: "chain", lane: null },
    critical_lanes: [],
    calendar_wait_s: 0,
    chains: CHAINS,
    tasks: [],
    skip_lanes: [],
    ...overrides,
  };
}

const NO_TARGET = criticalPath({
  target: null,
  set_finish: UNKNOWN,
  finish_after: UNKNOWN,
  finish_parallel: UNKNOWN,
  work_finish: [],
  bound: null,
  calendar_wait_s: null,
  chains: [],
});

function planOut(overrides: Record<string, unknown> = {}) {
  return {
    plan_id: 8,
    now: NOW,
    timezone: PLAN_ZONE,
    horizon_days: 90,
    horizon_end: NOW + 90 * 86_400,
    engine: "auto",
    profile: "fast",
    reproducible: false,
    input_hash: "i".repeat(64),
    score: 12.5,
    proof: HEURISTIC,
    pack: PACK,
    assignments: [],
    excluded: [],
    warnings: [],
    stale: false,
    strategy: null,
    composition: [],
    goal: null,
    war_windows: [],
    critical_path: false,
    town_hall_skip_lanes: [],
    composition_first: false,
    ...overrides,
  };
}

const NO_PLAN: Reply = {
  status: 404,
  body: { detail: { code: "no_plan", message: "Aucun plan." } },
};

type DashboardWorld = {
  path?: Record<string, unknown>;
  critical?: Handler;
  latest?: Reply;
};

function dashboard(world: DashboardWorld = {}): Handler {
  return (call) => {
    if (call.method !== "GET") {
      return undefined;
    }
    if (call.path === `${VILLAGE}/schedule`) {
      return { body: PROFILE };
    }
    if (call.path === "/api/gamedata/packs") {
      return { body: [] };
    }
    if (call.path === LATEST) {
      return world.latest ?? { body: planOut() };
    }
    if (call.path === CRITICAL) {
      return world.critical ? world.critical(call) : { body: world.path ?? criticalPath() };
    }
    return undefined;
  };
}

// ----------------------------------------------------------------------------------- helpers

function open(route: string): void {
  localStorage.setItem("clashpilot.village", TAG);
  window.history.pushState({}, "", route);
}

/** The text of an element as it reads, every run of white space (non-breaking too) as one space. */
function textOf(node: Node): string {
  return (node.textContent ?? "").replace(/\s+/g, " ").trim();
}

function card(): HTMLElement {
  const found = document.querySelectorAll<HTMLElement>("[data-critical-path]");
  expect(found, "elements [data-critical-path]").toHaveLength(1);
  return found[0] as HTMLElement;
}

/** The critical path card, once it shows `text`. */
async function cardShowing(text: string): Promise<HTMLElement> {
  return waitFor(() => {
    const element = card();
    expect(textOf(element)).toContain(text);
    return element;
  });
}

function textsOf(scope: HTMLElement, selector: string): string[] {
  return [...scope.querySelectorAll(selector)].map(textOf);
}

/** The card text without its chain and lane entries. */
function summaryOf(element: HTMLElement): string {
  const copy = element.cloneNode(true) as HTMLElement;
  for (const entry of copy.querySelectorAll("[data-chain], [data-lane-work]")) {
    entry.replaceWith(" | ");
  }
  return textOf(copy);
}

/** Lets the requests still in flight answer and the page render them. */
function pause(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 80));
}

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("lang");
  installFetch(() => undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// ------------------------------------------------------------------------- dashboard: dates

describe("Tableau de bord: earliest town hall date", () => {
  it("reads critical-path and shows both dates in the schedule zone with the badge Estimé", async () => {
    installFetch(dashboard());
    open("/");
    render(<App />);

    const element = await cardShowing(`Hôtel de Ville 12 au plus tôt : ${PARALLEL_TEXT}`);
    await waitFor(() => {
      expect(textsOf(card(), '[data-date="parallel"]')).toEqual([
        expect.stringContaining(`Hôtel de Ville 12 au plus tôt : ${PARALLEL_TEXT}`),
      ]);
      expect(textsOf(card(), '[data-date="after"]')).toEqual([
        expect.stringContaining(`Si l'HDV attend tout le reste : ${AFTER_TEXT}`),
      ]);
    });
    expect(within(element).getAllByText("Estimé").length).toBeGreaterThan(0);
    expect(textOf(element)).not.toContain("Aucun Hôtel de Ville à améliorer");
    expect(textOf(element)).not.toMatch(/optimal/i);
    expect(callsTo("GET", CRITICAL).length).toBeGreaterThan(0);
  });

  it("says which chains fix the date and lists each chain", async () => {
    installFetch(dashboard());
    open("/");
    render(<App />);

    const element = await cardShowing(PARALLEL_TEXT);
    expect(summaryOf(element)).toMatch(
      /Fixée par : Roi d'essai\s*(?:,|·|;|\/|\+|et)\s*(?:et\s+)?Reine d'essai/,
    );
    expect(textsOf(element, "[data-chain]")).toEqual([
      expect.stringContaining("Roi d'essai : niveau 3 → 7"),
      expect.stringContaining("Reine d'essai : niveau 10 → 12"),
    ]);
  });

  it.each([
    ["builder", "les ouvriers"],
    ["lab", "le laboratoire"],
    ["blacksmith", "la forge"],
    ["pet_house", "la maison des animaux"],
  ])("says the work of the lane %s fixes the date, as %s", async (key, label) => {
    const lanes = ["builder", "lab", "blacksmith", "pet_house"].map((name, index) =>
      name === key
        ? lane(name, PET_END + index * 3_600, name === "builder" ? 5 : 1, WORK_S)
        : lane(name, SMITH_END + index * 3_600, 1, 3_600),
    );
    installFetch(
      dashboard({
        path: criticalPath({
          work_finish: lanes,
          bound: { kind: "work", lane: key },
          critical_lanes: [key],
        }),
      }),
    );
    open("/");
    render(<App />);

    const element = await cardShowing(PARALLEL_TEXT);
    expect(summaryOf(element)).toContain(`Fixée par : ${label} (${WORK_TEXT})`);
    expect(textsOf(element, "[data-chain]")).toEqual([
      expect.stringContaining("Roi d'essai : niveau 3 → 7"),
      expect.stringContaining("Reine d'essai : niveau 10 → 12"),
    ]);
  });

  it("shows the work end of each lane and marks the critical lanes only", async () => {
    installFetch(
      dashboard({
        path: criticalPath({
          bound: { kind: "work", lane: "lab" },
          critical_lanes: ["lab", "blacksmith"],
        }),
      }),
    );
    open("/");
    render(<App />);

    const element = await cardShowing(PARALLEL_TEXT);
    const entries = textsOf(element, "[data-lane-work]");
    expect(entries).toHaveLength(3);
    const expected: Array<[string, RegExp, boolean]> = [
      [BUILDER_END_TEXT, /ouvriers|constructeur/i, false],
      [LAB_END_TEXT, /labo/i, true],
      [SMITH_END_TEXT, /forge/i, true],
    ];
    for (const [date, name, critical] of expected) {
      const matching = entries.filter((entry) => entry.includes(date));
      expect(matching, `entry of ${date}`).toHaveLength(1);
      expect(matching[0]).toMatch(name);
      expect(matching[0]?.includes("Ne doit jamais s'arrêter"), `critical mark of ${date}`).toBe(
        critical,
      );
    }
    expect(summaryOf(element)).not.toContain("Ne doit jamais s'arrêter");
  });

  it("says how long sleep delays the date", async () => {
    installFetch(dashboard({ path: criticalPath({ calendar_wait_s: 27_000 }) }));
    open("/");
    render(<App />);

    await cardShowing("Le sommeil retarde cette date de 7 h 30 min");
  });

  it.each([
    ["0", 0],
    ["null", null],
  ])("says nothing about sleep when calendar_wait_s is %s", async (_name, wait) => {
    installFetch(dashboard({ path: criticalPath({ calendar_wait_s: wait }) }));
    open("/");
    render(<App />);

    const element = await cardShowing(PARALLEL_TEXT);
    expect(textOf(element)).not.toContain("Le sommeil retarde");
  });

  it("says there is no town hall to upgrade when target is null, without any date", async () => {
    installFetch(dashboard({ path: NO_TARGET }));
    open("/");
    render(<App />);

    const element = await cardShowing("Aucun Hôtel de Ville à améliorer");
    const text = textOf(element);
    expect(text).not.toMatch(/\d{2}\/\d{2}\/\d{4}/);
    expect(text).not.toContain("au plus tôt :");
    expect(text).not.toContain("Fixée par");
    expect(element.querySelectorAll("[data-chain], [data-lane-work]")).toHaveLength(0);
  });

  it("shows a missing_input error as a problem notice with the way to the manual input", async () => {
    const message = "Des données manquent pour le chemin critique : builders_total.";
    installFetch(
      dashboard({
        latest: NO_PLAN,
        critical: () => ({
          status: 409,
          body: { detail: { code: "missing_input", message, missing: ["builders_total"] } },
        }),
      }),
    );
    open("/");
    render(<App />);

    await waitFor(() => {
      const alert = screen.queryAllByRole("alert").find((a) => textOf(a).includes(message));
      expect(alert, "alert with the API message").toBeDefined();
      expect(alert?.querySelector('a[href="/importer#saisie"]')).not.toBeNull();
    });
    expect(document.body.textContent).not.toContain("au plus tôt :");
  });
});

// ------------------------------------------------------------------- dashboard: skipped lanes

describe("Tableau de bord: lanes the latest plan does not wait for", () => {
  /** Answers the skipped-lab date only when `lab` is asked to be skipped. */
  const bySkip: Handler = (call) => {
    const skipped = new URLSearchParams(call.search).getAll("skip_lanes");
    return {
      body: criticalPath({
        finish_parallel: estimated(skipped.includes("lab") ? PARALLEL : PARALLEL_ALL),
        skip_lanes: skipped,
      }),
    };
  };
  const lastSkipped = () =>
    new URLSearchParams(callsTo("GET", CRITICAL).at(-1)?.search ?? "").getAll("skip_lanes").sort();

  it.each([[["lab"]], [["blacksmith", "lab"]]])(
    "calls critical-path with the skip_lanes %j and says the lab is not awaited",
    async (skip) => {
      installFetch(
        dashboard({ critical: bySkip, latest: { body: planOut({ town_hall_skip_lanes: skip }) } }),
      );
      open("/");
      render(<App />);

      const element = await cardShowing(`au plus tôt : ${PARALLEL_TEXT}`);
      expect(textOf(element)).toContain("Le laboratoire n'est pas attendu");
      expect(lastSkipped()).toEqual(skip);
    },
  );

  it.each([
    ["a plan skipping no lane", { body: planOut() }, []],
    [
      "a plan skipping the forge only",
      { body: planOut({ town_hall_skip_lanes: ["blacksmith"] }) },
      ["blacksmith"],
    ],
    ["no plan", NO_PLAN, []],
  ])("does not say the lab is not awaited with %s", async (_name, latest, skip) => {
    installFetch(dashboard({ critical: bySkip, latest }));
    open("/");
    render(<App />);

    await cardShowing(`au plus tôt : ${PARALLEL_ALL_TEXT}`);
    await pause();
    expect(textOf(card())).toContain(`au plus tôt : ${PARALLEL_ALL_TEXT}`);
    expect(textOf(card())).not.toContain("Le laboratoire n'est pas attendu");
    expect(lastSkipped()).toEqual(skip);
  });
});

// ------------------------------------------------------------------- Que faire maintenant ?

function action(overrides: Record<string, unknown>) {
  return {
    task_id: "4000001:0:5",
    name: "Barbare d'essai",
    action: "start",
    at: NOW,
    end: NOW + 3_600,
    lane: "lab",
    lane_slot: 0,
    why: { reasons: [{ code: "best_ratio", message: "Meilleur rapport.", value: null }] },
    advantages: [],
    drawbacks: [],
    reliability: { level: "high", factors: [] },
    counterfactual: null,
    float_s: null,
    critical: null,
    date_gain_s: null,
    ...overrides,
  };
}

const ACTIONS = [
  action({ float_s: 0, critical: true, date_gain_s: 5_400 }),
  action({
    task_id: "4000002:0:5",
    name: "Canon d'essai",
    lane: "builder",
    float_s: 93_600,
    critical: false,
    date_gain_s: 0,
  }),
  action({
    task_id: "4000003:0:2",
    name: "Mortier d'essai",
    lane: "builder",
    lane_slot: 1,
    float_s: null,
    critical: false,
    date_gain_s: null,
  }),
  action({
    task_id: "28000001:0:4",
    name: "Roi d'essai",
    lane: "builder",
    lane_slot: 2,
    float_s: 240,
    critical: true,
    date_gain_s: 0,
  }),
  action({
    task_id: null,
    name: "Attendre la fin de l'amélioration",
    action: "wait",
    at: NOW + 7_200,
    end: null,
    lane: null,
    lane_slot: null,
  }),
];

function assignment(taskId: string, name: string) {
  return {
    task_id: taskId,
    data_id: Number(taskId.split(":")[0]),
    name,
    lane: "builder",
    lane_slot: 0,
    start: NOW,
    end: NOW + 3_600,
    status: "planned",
    requires: [],
  };
}

const ASSIGNMENTS = [
  assignment("4000001:0:5", "Barbare d'essai"),
  assignment("4000002:0:5", "Canon d'essai"),
  assignment("4000003:0:2", "Mortier d'essai"),
  assignment("28000001:0:4", "Roi d'essai"),
];

function now(options: { actions?: unknown[]; alerts?: unknown[]; latest?: Reply } = {}): Handler {
  return (call) => {
    if (call.method !== "GET") {
      return undefined;
    }
    if (call.path === RECO) {
      return {
        body: {
          plan_id: 8,
          now: NOW,
          timezone: ZONE,
          fictional: false,
          pack: PACK,
          proof: HEURISTIC,
          actions: options.actions ?? ACTIONS,
          next_login_at: NOW,
          critical_alerts: options.alerts ?? [],
        },
      };
    }
    if (call.path === `${VILLAGE}/schedule`) {
      return { body: PROFILE };
    }
    if (call.path === LATEST) {
      return options.latest ?? { body: planOut({ assignments: ASSIGNMENTS }) };
    }
    return undefined;
  };
}

/** The text of the row of the action named `name`. */
async function rowText(name: string): Promise<string> {
  const [first] = await screen.findAllByText(name);
  const row = first?.closest<HTMLElement>("li, tr, article, [role='row'], [role='listitem']");
  if (!row) {
    throw new Error(`"${name}" is not inside a row`);
  }
  return textOf(row);
}

describe("Que faire maintenant ?: critical actions", () => {
  it("says Critique for a critical start and Marge with the float otherwise", async () => {
    installFetch(now());
    open("/maintenant");
    render(<App />);

    const critical = await rowText("Barbare d'essai");
    expect(critical).toContain("Critique");
    expect(critical).not.toContain("Marge");

    const nearly = await rowText("Roi d'essai");
    expect(nearly).toContain("Critique");
    expect(nearly).not.toContain("Marge");

    const slack = await rowText("Canon d'essai");
    expect(slack).toContain("Marge 1 j 2 h");
    expect(slack).not.toContain("Critique");

    const unknown = await rowText("Mortier d'essai");
    expect(unknown).not.toContain("Marge");
    expect(unknown).not.toContain("Critique");

    const waiting = await rowText("Attendre la fin de l'amélioration");
    expect(waiting).not.toContain("Marge");
    expect(waiting).not.toContain("Critique");
  });

  it("says how much a start brings the date forward, only above zero", async () => {
    installFetch(now());
    open("/maintenant");
    render(<App />);

    expect(await rowText("Barbare d'essai")).toContain("Avance ta date de 1 h 30 min");
    for (const name of [
      "Canon d'essai",
      "Mortier d'essai",
      "Roi d'essai",
      "Attendre la fin de l'amélioration",
    ]) {
      expect(await rowText(name), name).not.toContain("Avance ta date");
    }
  });

  it("puts the badge Ta compo on the actions of the latest plan composition only", async () => {
    // 5 is also the level of two actions: only the data id counts.
    installFetch(
      now({
        latest: { body: planOut({ assignments: ASSIGNMENTS, composition: [4000001, 5] }) },
      }),
    );
    open("/maintenant");
    render(<App />);

    await waitFor(async () => expect(await rowText("Barbare d'essai")).toContain("Ta compo"));
    for (const name of [
      "Canon d'essai",
      "Mortier d'essai",
      "Roi d'essai",
      "Attendre la fin de l'amélioration",
    ]) {
      expect(await rowText(name), name).not.toContain("Ta compo");
    }
  });

  it.each([
    ["an empty composition", { body: planOut({ assignments: ASSIGNMENTS }) }],
    ["no readable latest plan", NO_PLAN],
  ])("keeps the actions and shows no Ta compo with %s", async (_name, latest) => {
    installFetch(now({ latest }));
    open("/maintenant");
    render(<App />);

    expect(await rowText("Barbare d'essai")).toContain("Critique");
    await waitFor(() => expect(callsTo("GET", LATEST).length).toBeGreaterThan(0));
    await pause();
    expect(document.body.textContent).not.toContain("Ta compo");
  });
});

const STOPPED = "est à l'arrêt : chaque heure d'arrêt retarde ton Hôtel de Ville";

function chainStopped(name: string, delay: number | null) {
  return {
    code: "chain_stopped",
    task_id: "28000009:0:4",
    name,
    planned_start: delay === null ? null : NOW + delay,
    delay_s: delay,
  };
}

function laneStopped(key: string, delay: number | null) {
  return {
    code: "lane_stopped",
    lane: key,
    planned_start: delay === null ? null : NOW + delay,
    delay_s: delay,
  };
}

/** The text of the one alert that matches `pattern`. */
async function alertText(pattern: RegExp): Promise<string> {
  return waitFor(() => {
    const found = screen
      .queryAllByRole("alert")
      .map(textOf)
      .filter((text) => pattern.test(text));
    expect(found, `alerts matching ${pattern}`).toHaveLength(1);
    return found[0] as string;
  });
}

describe("Que faire maintenant ?: stopped critical work", () => {
  it("announces each stopped chain and lane in its own alert, with the planned delay", async () => {
    installFetch(
      now({
        alerts: [
          chainStopped("Gardienne d'essai", 5_400),
          chainStopped("Championne d'essai", null),
          laneStopped("lab", 10_800),
        ],
      }),
    );
    open("/maintenant");
    render(<App />);

    const planned = await alertText(/Gardienne d'essai/);
    expect(planned).toContain(`Gardienne d'essai ${STOPPED}`);
    expect(planned).toContain("(1 h 30 min prévues)");

    const unplanned = await alertText(/Championne d'essai/);
    expect(unplanned).toContain(`Championne d'essai ${STOPPED}`);
    expect(unplanned).not.toContain("prévues");

    const lab = await alertText(/laboratoire/i);
    expect(lab).toMatch(new RegExp(`[Ll]e laboratoire ${STOPPED}`));
    expect(lab).toContain("(3 h prévues)");

    expect(
      screen.getAllByRole("alert").filter((a) => textOf(a).includes("à l'arrêt")),
    ).toHaveLength(3);
  });

  it.each([
    ["builder", /[Ll]es ouvriers (?:est|sont) à l'arrêt/],
    ["lab", /[Ll]e laboratoire est à l'arrêt/],
    ["blacksmith", /[Ll]a forge est à l'arrêt/],
    ["pet_house", /[Ll]a maison des animaux est à l'arrêt/],
  ])("names the stopped lane %s, even with no action to start", async (key, pattern) => {
    installFetch(now({ actions: [], alerts: [laneStopped(key, null)] }));
    open("/maintenant");
    render(<App />);

    const text = await alertText(pattern);
    expect(text).toContain("chaque heure d'arrêt retarde ton Hôtel de Ville");
    expect(text).not.toContain("prévues");
  });
});

// ---------------------------------------------------------------------------------- Stratégie

const CRITICAL_BOX = "Viser la date d'HDV au plus tôt";
const SKIP_BOX = "L'Hôtel de Ville n'attend pas le laboratoire";
const FIRST_BOX = /^Laboratoire\s:\sma composition d'abord$/;
const FIRST_HINT = "Choisis d'abord tes troupes et tes sorts";

const strategyWorld: Handler = (call) => {
  if (call.method === "GET" && call.path === "/api/meta/strategies") {
    return {
      body: { strategies: [{ key: "finish_th", label: "Finir l'Hôtel de Ville", weights: {} }] },
    };
  }
  if (call.method === "GET" && call.path === `${VILLAGE}/schedule`) {
    return { body: PROFILE };
  }
  if (call.method === "POST" && call.path === PLANS) {
    return { status: 201, body: planOut() };
  }
  return undefined;
};

async function openStrategy() {
  installFetch(strategyWorld);
  open("/strategie");
  render(<App />);
  await screen.findByRole("radio", { name: "Finir l'Hôtel de Ville" });
  return userEvent.setup();
}

function box(name: string | RegExp): HTMLInputElement {
  return screen.getByRole("checkbox", { name }) as HTMLInputElement;
}

function isDisabled(element: HTMLInputElement): boolean {
  return element.disabled || element.getAttribute("aria-disabled") === "true";
}

function typeComposition(text: string): void {
  fireEvent.change(screen.getByRole("textbox", { name: "Composition" }), {
    target: { value: text },
  });
}

/** Presses "Calculer mon plan" and returns the body of the request it sent. */
async function computed(user: ReturnType<typeof userEvent.setup>): Promise<unknown> {
  const before = callsTo("POST", PLANS).length;
  await user.click(screen.getByRole("button", { name: "Calculer mon plan" }));
  await waitFor(() => expect(callsTo("POST", PLANS)).toHaveLength(before + 1));
  await screen.findByText("Plan calculé.");
  const sent = callsTo("POST", PLANS)[before]?.body ?? "";
  return sent === "" ? {} : JSON.parse(sent);
}

describe("Stratégie: critical path options", () => {
  it("sends the E20 body, without any new key, while the three boxes are unchecked", async () => {
    const user = await openStrategy();
    for (const name of [CRITICAL_BOX, SKIP_BOX, FIRST_BOX]) {
      expect(box(name).checked, String(name)).toBe(false);
    }

    expect(await computed(user)).toEqual({});

    await user.click(screen.getByRole("radio", { name: "Finir l'Hôtel de Ville" }));
    typeComposition("4000001, 4000002");
    expect(await computed(user)).toEqual({
      strategy: "finish_th",
      composition: [4000001, 4000002],
    });
  });

  it("sends critical_path true when Viser la date d'HDV au plus tôt is checked, only then", async () => {
    const user = await openStrategy();

    await user.click(box(CRITICAL_BOX));
    expect(await computed(user)).toEqual({ critical_path: true });

    await user.click(box(CRITICAL_BOX));
    expect(await computed(user)).toEqual({});
  });

  it("sends town_hall_skip_lanes lab when the town hall does not wait for the lab", async () => {
    const user = await openStrategy();

    await user.click(box(SKIP_BOX));
    expect(await computed(user)).toEqual({ town_hall_skip_lanes: ["lab"] });

    await user.click(box(SKIP_BOX));
    expect(await computed(user)).toEqual({});
  });

  it("only lets the composition come first once a composition is typed", async () => {
    const user = await openStrategy();

    expect(isDisabled(box(FIRST_BOX))).toBe(true);
    expect(textOf(document.body)).toContain(FIRST_HINT);
    await user.click(box(FIRST_BOX));
    expect(box(FIRST_BOX).checked).toBe(false);
    typeComposition("   ");
    expect(isDisabled(box(FIRST_BOX))).toBe(true);

    typeComposition("4000001");
    expect(isDisabled(box(FIRST_BOX))).toBe(false);
    expect(await computed(user)).toEqual({ composition: [4000001] });

    await user.click(box(FIRST_BOX));
    expect(await computed(user)).toEqual({ composition: [4000001], composition_first: true });

    await user.click(box(FIRST_BOX));
    expect(await computed(user)).toEqual({ composition: [4000001] });
  });

  it("drops composition_first when the composition is emptied after the box was checked", async () => {
    const user = await openStrategy();

    typeComposition("4000001");
    await user.click(box(FIRST_BOX));
    expect(box(FIRST_BOX).checked).toBe(true);
    typeComposition("");

    expect(isDisabled(box(FIRST_BOX))).toBe(true);
    expect(await computed(user)).toEqual({});
  });

  it("sends the three options together with the strategy and the composition", async () => {
    const user = await openStrategy();

    await user.click(screen.getByRole("radio", { name: "Finir l'Hôtel de Ville" }));
    typeComposition("4000001");
    await user.click(box(CRITICAL_BOX));
    await user.click(box(SKIP_BOX));
    await user.click(box(FIRST_BOX));

    expect(await computed(user)).toEqual({
      strategy: "finish_th",
      composition: [4000001],
      critical_path: true,
      town_hall_skip_lanes: ["lab"],
      composition_first: true,
    });
  });
});
