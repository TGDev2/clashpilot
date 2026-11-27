/**
 * E28 acceptance tests: the page "Quand lancer l'Hôtel de Ville ?" and its README section (frozen
 * with the exam commit).
 *
 * Contract of the UI these tests drive (everything is French, labels come from `fr.ts`; the UI
 * computes no plan: it shows what the API answers, the only arithmetic being the gap with `after`).
 * The current village is `localStorage["clashpilot.village"]`; the tests set it, render `App` at the
 * route and drive the page. Texts are read on `textContent` with white space collapsed, so sibling
 * elements need real text nodes between them. Durations are those of `formatDuration`.
 *
 * Route `/hdv`, a link `href="/hdv"` in the navigation of `Layout`, a heading "Quand lancer l'Hôtel
 * de Ville ?". The page sits behind `PlanGate`: without a plan (404 `no_plan`) or a village, "Aucun
 * plan" with its link to `/maintenant`, and no button.
 *
 * - A checkbox named exactly "L'Hôtel de Ville n'attend pas le laboratoire", checked at first when
 *   `town_hall_skip_lanes` of `GET .../plans/latest` holds "lab".
 * - A button "Comparer" sends `POST .../town-hall/compare` (nothing is sent before it is pressed).
 *   The body holds `skip_lanes` and no other key: it holds "lab" exactly when the box is checked
 *   (an empty list is sent as such, since an absent key means the lanes of the latest plan).
 * - One element `[data-option="{kind}"]` per option of the latest answer, in the API order, holding
 *   its label: "Après tout le reste" (`after`), "Sans ouvrier inactif" (`no_wait`), "À la fin de la
 *   réduction" (`discount_edge`, which the answer may not hold).
 * - In an `ok` row: `th_start` then `th_finish` written by `formatInZone` in the zone `tz` of
 *   `GET .../schedule` (not the zone of the plan); the duration of `idle_builder_s`; one
 *   `[data-lane="{lane}"]` per entry of `unfinished_by_lane` (and no other) with its count and the
 *   duration of `unfinished_work_s_by_lane`, written "{n} recherches, {durée} de labo" for the lab
 *   and naming the lane otherwise (ouvriers or constructeurs, forge); one `[data-hero]` per entry of
 *   `heroes` with its name and `level_reached`; "{count} recherches de ta compo non finies
 *   ({durée})" from `unfinished_composition`, a count of 0 included.
 * - A row other than `after` also shows `formatSigned(unfinished_count - after.unfinished_count)`,
 *   the gap in unfinished upgrades. The `after` row shows no signed number, and no row does when
 *   `after` is infeasible.
 * - An `infeasible` row shows the message of each of its reasons, and no date, duration, lane, hero
 *   or gap.
 * - A 404 or 409 answer of the comparison is a `ProblemNotice`: the API message in a `role="alert"`,
 *   with the link `/importer#saisie` for `missing_input`.
 * - The page never says "optimal", whatever the proof of an option.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "../App";

// ---------------------------------------------------------------------------------- fetch stub

type Call = { method: string; path: string; body: string };
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

function posts(): Call[] {
  return calls.filter((c) => c.method === "POST");
}

// -------------------------------------------------------------------------------------- fixtures

const TAG = "P2Y8QQ0";
const VILLAGE = `/api/villages/${TAG}`;
const LATEST = `${VILLAGE}/plans/latest`;
const COMPARE = `${VILLAGE}/town-hall/compare`;
const ZONE = "Asia/Kolkata"; // UTC+5:30, no daylight saving time
const PLAN_ZONE = "America/Lima"; // the page must not use it

function epoch(year: number, month: number, day: number, hour: number, minute: number): number {
  return Date.UTC(year, month - 1, day, hour, minute) / 1000;
}

function span(days: number, hours = 0, minutes = 0): number {
  return days * 86_400 + hours * 3_600 + minutes * 60;
}

const NOW = epoch(2026, 11, 12, 12, 0);

const PROFILE = {
  tz: ZONE,
  weekly: [],
  exceptions: [],
  policy: { mode: "strict", tolerance_minutes: 0, max_logins_per_day: null },
};

const PACK = { pack_id: "pack-alpha", game_version: "7.1-test", fictional: false };
const HEURISTIC = { kind: "heuristic", objective: 10, bound: null, gap_abs: null, scope: "" };

function planOut(skipLanes: string[] = []) {
  return {
    plan_id: 8,
    now: NOW,
    timezone: PLAN_ZONE,
    horizon_days: 90,
    horizon_end: NOW + span(90),
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
    composition: [4000001],
    goal: null,
    war_windows: [],
    critical_path: false,
    town_hall_skip_lanes: skipLanes,
    composition_first: false,
  };
}

const AFTER = {
  kind: "after",
  status: "ok",
  reasons: [],
  th_start: epoch(2026, 12, 19, 22, 45),
  th_finish: epoch(2027, 1, 3, 13, 30),
  idle_builder_s: span(9, 4),
  unfinished_count: 8,
  unfinished_by_lane: { blacksmith: 2, lab: 6 },
  unfinished_work_s_by_lane: { blacksmith: span(1, 11), lab: span(4, 5) },
  unfinished_composition: { count: 4, work_s: span(2, 7) },
  heroes: [
    { data_id: 28000001, name: "Roi d'essai", level_reached: 12 },
    { data_id: 28000002, name: "Reine d'essai", level_reached: 9 },
  ],
  engine: "auto",
  proof: HEURISTIC,
};

const NO_WAIT = {
  kind: "no_wait",
  status: "ok",
  reasons: [],
  th_start: epoch(2026, 11, 25, 3, 30),
  th_finish: epoch(2026, 12, 10, 12, 0),
  idle_builder_s: span(0, 7, 45),
  unfinished_count: 25,
  unfinished_by_lane: { blacksmith: 3, builder: 14, lab: 8 },
  unfinished_work_s_by_lane: {
    blacksmith: span(0, 5, 20),
    builder: span(31, 2),
    lab: span(17, 6, 10),
  },
  unfinished_composition: { count: 11, work_s: span(12, 0, 30) },
  heroes: [
    { data_id: 28000001, name: "Roi d'essai", level_reached: 7 },
    { data_id: 28000002, name: "Reine d'essai", level_reached: 5 },
  ],
  engine: "auto",
  // A proof that `ProofLabel` words as "optimale": the page must not say it.
  proof: {
    kind: "optimal_window",
    objective: 11,
    bound: 11,
    gap_abs: 0,
    scope: "fenêtre 3 j, grille 5 min, objectif discrétisé",
  },
};

const DISCOUNT_EDGE = {
  kind: "discount_edge",
  status: "ok",
  reasons: [],
  th_start: epoch(2026, 11, 18, 14, 45),
  th_finish: epoch(2026, 12, 3, 14, 45),
  idle_builder_s: span(2, 6),
  unfinished_count: 5,
  unfinished_by_lane: { builder: 2, lab: 3 },
  unfinished_work_s_by_lane: { builder: span(6, 13), lab: span(3, 0, 40) },
  unfinished_composition: { count: 0, work_s: 0 },
  heroes: [{ data_id: 28000001, name: "Roi d'essai", level_reached: 10 }],
  engine: "auto",
  proof: { kind: "feasible", objective: 9, bound: 12, gap_abs: 3, scope: "" },
};

const REASONS = [
  { code: "insufficient_funds", message: "Les réserves ne suffiront pas à lancer l'amélioration." },
  { code: "no_slot", message: "Aucun instant de l'horizon ne permet ce lancement." },
];

function infeasible(kind: string) {
  return {
    kind,
    status: "infeasible",
    reasons: REASONS,
    th_start: null,
    th_finish: null,
    idle_builder_s: null,
    unfinished_count: null,
    unfinished_by_lane: {},
    unfinished_work_s_by_lane: {},
    unfinished_composition: null,
    heroes: [],
    engine: "auto",
    proof: null,
  };
}

function comparison(options: unknown[] = [AFTER, NO_WAIT, DISCOUNT_EDGE]): Reply {
  return {
    body: {
      target: {
        task_id: "1000001:0:12",
        data_id: 1000001,
        name: "Hôtel de Ville",
        to_level: 12,
      },
      skip_lanes: [],
      options,
      reference_plan_id: 8,
      now: NOW,
      scope: "Plan n° 8, horizon 90 j, moteur auto.",
    },
  };
}

const NO_PLAN: Reply = {
  status: 404,
  body: { detail: { code: "no_plan", message: "Aucun plan." } },
};

type World = { latest?: Reply; compare?: () => Reply };

function world({ latest, compare }: World = {}): Handler {
  return (call) => {
    if (call.method === "POST") {
      return call.path === COMPARE ? (compare?.() ?? comparison()) : undefined;
    }
    if (call.path === `${VILLAGE}/schedule`) {
      return { body: PROFILE };
    }
    if (call.path === LATEST) {
      return latest ?? { body: planOut() };
    }
    if (call.path === "/api/gamedata/packs") {
      return { body: [] };
    }
    return undefined;
  };
}

// ----------------------------------------------------------------------------------- helpers

const TITLE = /^Quand lancer l'Hôtel de Ville\s\?$/;
const SKIP_BOX = "L'Hôtel de Ville n'attend pas le laboratoire";
const LABELS = {
  after: "Après tout le reste",
  no_wait: "Sans ouvrier inactif",
  discount_edge: "À la fin de la réduction",
};
/** A number written with its sign, as `formatSigned` writes a gap. */
const SIGNED = /(?<![\p{L}\d])[+\-−]\s?\d/u;
const DATE = /\d{2}\/\d{2}\/\d{4}/;

function open(route: string, village: string | null = TAG): void {
  if (village !== null) {
    localStorage.setItem("clashpilot.village", village);
  }
  window.history.pushState({}, "", route);
}

/** The text of an element as it reads, every run of white space (non-breaking too) as one space. */
function textOf(node: Node): string {
  return (node.textContent ?? "").replace(/\s+/g, " ").trim();
}

/** Lets the requests still in flight answer and the page render them. */
function pause(): Promise<void> {
  return new Promise((done) => setTimeout(done, 80));
}

function kinds(): Array<string | null> {
  return [...document.querySelectorAll("[data-option]")].map((e) => e.getAttribute("data-option"));
}

function row(kind: string): HTMLElement {
  const found = document.querySelectorAll<HTMLElement>(`[data-option="${kind}"]`);
  expect(found, `rows of the option ${kind}`).toHaveLength(1);
  return found[0] as HTMLElement;
}

async function pressCompare(user: ReturnType<typeof userEvent.setup>): Promise<void> {
  const before = posts().length;
  const button = await screen.findByRole("button", { name: "Comparer" });
  await waitFor(() => expect((button as HTMLButtonElement).disabled).toBe(false));
  await user.click(button);
  await waitFor(() => expect(posts()).toHaveLength(before + 1));
}

/** Opens `/hdv` on `settings`, presses "Comparer" and waits for the rows of `expected`. */
async function compared(
  options: unknown[] = [AFTER, NO_WAIT, DISCOUNT_EDGE],
  expected: string[] = ["after", "no_wait", "discount_edge"],
): Promise<void> {
  installFetch(world({ compare: () => comparison(options) }));
  open("/hdv");
  render(<App />);
  await pressCompare(userEvent.setup());
  await waitFor(() => expect(kinds()).toEqual(expected));
}

function laneEntry(scope: HTMLElement, lane: string): string {
  const found = scope.querySelectorAll(`[data-lane="${lane}"]`);
  expect(found, `entries of the lane ${lane}`).toHaveLength(1);
  return textOf(found[0] as Element);
}

/** A lane entry other than the lab: its name, its duration and its count apart from the duration. */
function expectLane(entry: string, name: RegExp, count: number, duration: string): void {
  expect(entry).toMatch(name);
  expect(entry).toContain(duration);
  expect(entry.replace(duration, " ")).toMatch(new RegExp(`(?<!\\d)${count}(?!\\d)`));
}

function expectHeroes(scope: HTMLElement, heroes: Array<[string, number]>): void {
  const entries = [...scope.querySelectorAll("[data-hero]")].map(textOf);
  expect(entries).toHaveLength(heroes.length);
  for (const [name, level] of heroes) {
    const matching = entries.filter((entry) => entry.includes(name));
    expect(matching, `entry of ${name}`).toHaveLength(1);
    expect(matching[0]).toMatch(new RegExp(`(?<!\\d)${level}(?!\\d)`));
  }
}

/** Waits for the launch and the end of a row, in this order. */
async function expectDates(kind: string, start: string, finish: string): Promise<void> {
  await waitFor(() => {
    const text = textOf(row(kind));
    expect(text).toContain(start);
    expect(text).toContain(finish);
    expect(text.indexOf(start)).toBeLessThan(text.indexOf(finish));
  });
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

// ----------------------------------------------------------------------------- route and gate

describe("/hdv: route, navigation and plan gate", () => {
  it("is reached from the navigation and shows its title", async () => {
    installFetch(world());
    open("/");
    render(<App />);
    const user = userEvent.setup();

    const nav = screen.getByRole("navigation");
    const link = nav.querySelector<HTMLAnchorElement>('a[href="/hdv"]');
    expect(link, "link to /hdv in the navigation").not.toBeNull();
    expect(textOf(link as HTMLAnchorElement)).not.toBe("");
    await user.click(link as HTMLAnchorElement);

    expect(await screen.findByRole("heading", { name: TITLE })).toBeDefined();
    expect(window.location.pathname).toBe("/hdv");
    expect(await screen.findByRole("button", { name: "Comparer" })).toBeDefined();
  });

  it("sends nothing and shows no option before Comparer is pressed", async () => {
    installFetch(world());
    open("/hdv");
    render(<App />);

    await screen.findByRole("heading", { name: TITLE });
    await screen.findByRole("button", { name: "Comparer" });
    await pause();
    expect(posts()).toHaveLength(0);
    expect(kinds()).toEqual([]);
  });

  it.each([
    ["no plan", TAG],
    ["no village", null],
  ])("says Aucun plan with the way to compute one with %s", async (_name, village) => {
    installFetch(world({ latest: NO_PLAN }));
    open("/hdv", village);
    render(<App />);

    await screen.findByRole("heading", { name: TITLE });
    const main = screen.getByRole("main");
    await waitFor(() => {
      expect(textOf(main)).toContain("Aucun plan");
      expect(main.querySelector('a[href="/maintenant"]')).not.toBeNull();
    });
    await pause();
    expect(within(main).queryByRole("button", { name: "Comparer" })).toBeNull();
    expect(posts()).toHaveLength(0);
  });
});

// ------------------------------------------------------------------------------- comparison

describe("/hdv: options of the comparison", () => {
  it("posts the comparison and shows one labelled row per option", async () => {
    await compared();

    expect(posts().map((c) => c.path)).toEqual([COMPARE]);
    for (const [kind, label] of Object.entries(LABELS)) {
      expect(textOf(row(kind))).toContain(label);
    }
  });

  it("shows only the options of the latest answer, without discount_edge when it is absent", async () => {
    let answers = 0;
    installFetch(
      world({
        compare: () => {
          answers += 1;
          return answers === 1 ? comparison() : comparison([AFTER, NO_WAIT]);
        },
      }),
    );
    open("/hdv");
    render(<App />);
    const user = userEvent.setup();

    await pressCompare(user);
    await waitFor(() => expect(kinds()).toEqual(["after", "no_wait", "discount_edge"]));
    await pressCompare(user);
    await waitFor(() => expect(kinds()).toEqual(["after", "no_wait"]));
  });

  it("shows the launch and the end of the town hall in the schedule zone", async () => {
    await compared();

    await expectDates("after", "20/12/2026 04:15", "03/01/2027 19:00");
    await expectDates("no_wait", "25/11/2026 09:00", "10/12/2026 17:30");
    await expectDates("discount_edge", "18/11/2026 20:15", "03/12/2026 20:15");
  });

  it("shows the idle builder time of each option", async () => {
    await compared();

    expect(textOf(row("after"))).toContain("9 j 4 h");
    expect(textOf(row("no_wait"))).toContain("7 h 45 min");
    expect(textOf(row("discount_edge"))).toContain("2 j 6 h");
  });

  it("shows the unfinished upgrades of each lane at the arrival", async () => {
    await compared();

    const after = row("after");
    expect(after.querySelectorAll("[data-lane]")).toHaveLength(2);
    expect(laneEntry(after, "lab")).toContain("6 recherches, 4 j 5 h de labo");
    expectLane(laneEntry(after, "blacksmith"), /forge/i, 2, "1 j 11 h");

    const noWait = row("no_wait");
    expect(noWait.querySelectorAll("[data-lane]")).toHaveLength(3);
    expect(laneEntry(noWait, "lab")).toContain("8 recherches, 17 j 6 h 10 min de labo");
    expectLane(laneEntry(noWait, "builder"), /ouvriers|constructeur/i, 14, "31 j 2 h");
    expectLane(laneEntry(noWait, "blacksmith"), /forge/i, 3, "5 h 20 min");

    const edge = row("discount_edge");
    expect(edge.querySelectorAll("[data-lane]")).toHaveLength(2);
    expect(laneEntry(edge, "lab")).toContain("3 recherches, 3 j 40 min de labo");
    expectLane(laneEntry(edge, "builder"), /ouvriers|constructeur/i, 2, "6 j 13 h");
  });

  it("shows the level each hero reached", async () => {
    await compared();

    expectHeroes(row("after"), [
      ["Roi d'essai", 12],
      ["Reine d'essai", 9],
    ]);
    expectHeroes(row("no_wait"), [
      ["Roi d'essai", 7],
      ["Reine d'essai", 5],
    ]);
    expectHeroes(row("discount_edge"), [["Roi d'essai", 10]]);
  });

  it("shows the unfinished researches of the composition on each row", async () => {
    await compared();

    expect(textOf(row("after"))).toContain("4 recherches de ta compo non finies (2 j 7 h)");
    expect(textOf(row("no_wait"))).toContain("11 recherches de ta compo non finies (12 j 30 min)");
    expect(textOf(row("discount_edge"))).toContain("0 recherches de ta compo non finies (0 min)");
  });

  it("shows the signed gap with after on the other rows only", async () => {
    await compared();

    expect(textOf(row("no_wait"))).toMatch(/(?<![\p{L}\d])\+17(?!\d)/u);
    expect(textOf(row("discount_edge"))).toMatch(/(?<![\p{L}\d])-3(?!\d)/u);
    expect(textOf(row("after"))).not.toMatch(SIGNED);
  });

  it("shows no gap when after is infeasible", async () => {
    await compared([infeasible("after"), NO_WAIT], ["after", "no_wait"]);

    expect(textOf(row("no_wait"))).toContain("11 recherches de ta compo non finies");
    expect(textOf(row("no_wait"))).not.toMatch(SIGNED);
    expect(textOf(row("after"))).not.toMatch(SIGNED);
  });

  it("shows the reasons of an infeasible option and no figure", async () => {
    await compared([AFTER, NO_WAIT, infeasible("discount_edge")]);

    const failed = row("discount_edge");
    const text = textOf(failed);
    expect(text).toContain(LABELS.discount_edge);
    for (const reason of REASONS) {
      expect(text).toContain(reason.message);
    }
    expect(text).not.toMatch(DATE);
    expect(text).not.toContain("recherches");
    expect(text).not.toMatch(/\d\s?(?:j|h|min)(?!\p{L})/u);
    expect(text).not.toMatch(SIGNED);
    expect(failed.querySelectorAll("[data-lane], [data-hero]")).toHaveLength(0);
  });

  it("never says optimal, even for a proof of the window", async () => {
    await compared();

    await expectDates("no_wait", "25/11/2026 09:00", "10/12/2026 17:30");
    expect(document.body.textContent).not.toMatch(/optimal/i);
  });
});

// ----------------------------------------------------------------------------------- errors

describe("/hdv: errors of the comparison", () => {
  it.each([
    [404, "no_plan", "Aucun plan n'est encore calculé pour ce village."],
    [404, "village_not_found", "Ce village n'existe pas."],
    [409, "no_active_pack", "Aucun pack de données n'est actif."],
    [409, "missing_input", "Des données manquent pour comparer : builders_total."],
  ])("shows a %i %s answer as a problem notice", async (status, code, message) => {
    installFetch(
      world({
        compare: () => ({
          status,
          body: { detail: { code, message, missing: ["builders_total"] } },
        }),
      }),
    );
    open("/hdv");
    render(<App />);

    await pressCompare(userEvent.setup());

    await waitFor(() => {
      const alert = screen.queryAllByRole("alert").find((a) => textOf(a).includes(message));
      expect(alert, "alert with the API message").toBeDefined();
      if (code === "missing_input") {
        expect(alert?.querySelector('a[href="/importer#saisie"]')).not.toBeNull();
      }
    });
    expect(kinds()).toEqual([]);
    expect(document.body.textContent).not.toMatch(/optimal/i);
  });
});

// ------------------------------------------------------------------------------- skipped lab

describe("/hdv: the town hall does not wait for the lab", () => {
  function box(): HTMLInputElement {
    return screen.getByRole("checkbox", { name: SKIP_BOX }) as HTMLInputElement;
  }

  function sent(): { skip_lanes: string[] } {
    return JSON.parse(posts().at(-1)?.body ?? "null");
  }

  it.each([
    ["no lane", [], false],
    ["the lab", ["lab"], true],
    ["the forge", ["blacksmith"], false],
    ["the forge and the lab", ["blacksmith", "lab"], true],
  ])(
    "is checked from a latest plan skipping %s and sends skip_lanes",
    async (_name, lanes, checked) => {
      installFetch(world({ latest: { body: planOut(lanes) } }));
      open("/hdv");
      render(<App />);
      const user = userEvent.setup();
      await screen.findByRole("checkbox", { name: SKIP_BOX });
      await waitFor(() => expect(box().checked).toBe(checked));

      for (const state of [checked, !checked]) {
        if (box().checked !== state) {
          await user.click(box());
        }
        expect(box().checked).toBe(state);
        await pressCompare(user);

        const body = sent();
        expect(Object.keys(body)).toEqual(["skip_lanes"]);
        expect(body.skip_lanes.includes("lab")).toBe(state);
        for (const lane of body.skip_lanes) {
          expect([...lanes, "lab"]).toContain(lane);
        }
        if (lanes.every((lane) => lane === "lab")) {
          expect(body.skip_lanes).toEqual(state ? ["lab"] : []);
        }
        await waitFor(() => expect(kinds()).toHaveLength(3));
      }
    },
  );
});

// ------------------------------------------------------------------------------------ README

describe("README: town hall date and critical path", () => {
  /** The body of the section, up to the next heading of the same level or above. */
  function section(): string {
    // Vitest runs from `frontend/`.
    const readme = readFileSync(resolve(process.cwd(), "..", "README.md"), "utf8").replace(
      /\r\n/g,
      "\n",
    );
    const heading = /^(#{2,3}) +Date d['’]Hôtel de Ville et chemin critique *$/m.exec(readme);
    expect(heading, "section « Date d'Hôtel de Ville et chemin critique »").not.toBeNull();
    const rest = readme.slice((heading?.index ?? 0) + (heading?.[0].length ?? 0));
    const next = new RegExp(`^#{1,${heading?.[1]?.length ?? 2}} `, "m").exec(rest);
    return rest.slice(0, next?.index ?? rest.length);
  }

  it.each([
    ["the earliest date", /au plus tôt/i],
    ["the float", /\bmarges?\b/i],
    ["the critical_path option", /critical_path/],
    ["the /hdv page", /\/hdv\b/],
  ])("explains %s", (_name, pattern) => {
    expect(section()).toMatch(pattern);
  });

  it("states that the chain date ignores capacity and resources", () => {
    const blocks = section().split(/\n\s*\n|\n(?=\s*(?:[-*]|\d+\.) )/);
    const limit = blocks.filter(
      (block) => /cha[iî]ne/i.test(block) && /capacit/i.test(block) && /ressource/i.test(block),
    );
    expect(limit, "a paragraph or item naming chain, capacity and resources").not.toHaveLength(0);
  });
});
