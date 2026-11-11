/**
 * E15 acceptance tests: replanning from the UI, the journal page and the dashboard indicators
 * (frozen with the exam commit).
 *
 * Contract of the UI these tests drive (everything is French; the UI computes no plan and never
 * converts a time zone itself: epochs are shown with `Intl.DateTimeFormat` and the zone the API sent,
 * and a typed local time goes to the server). The current village is `localStorage["clashpilot.village"]`;
 * the tests set it, render `App` at the route and drive the page. Server errors are
 * `{detail: {code, message}}` and the French `message` is what the user reads.
 *
 * Que faire maintenant ? (`/maintenant`, E13). The plan the page replans is the one of
 * `GET .../recommendations/now` (`plan_id`); the events go to `POST .../plans/{plan_id}/events`.
 * - Every `start` action (non-null `task_id`) has, inside its row (`li`, `tr`, `article`, role row or
 *   listitem), the buttons "Je l'ai lancée" and "Simuler un retard". A `wait` action has neither.
 * - "Je l'ai lancée" opens an element with `role="dialog"` holding a native `<input>` (datetime-local or
 *   text) labelled "Heure réelle de lancement", prefilled with the `now` of the recommendations answer as a
 *   local date-time of the answer's `timezone`, `AAAA-MM-JJTHH:MM` (seconds allowed), no zone suffix, and
 *   the buttons "Valider" and "Annuler". "Annuler" closes it and sends nothing. "Valider" sends
 *   `POST /api/villages/{id}/schedule/to-epoch` with `{local_datetime}` (the typed text as is), waits for
 *   `{epoch}`, then sends the event `{type: "task_started", task_id, at: epoch}` without `dry_run`
 *   (absent or false) and closes the dialog.
 * - "Simuler un retard" shows the choices: the buttons "15 min" and "1 h", and a number field labelled
 *   "Durée libre (minutes)" with the button "Simuler". Each choice sends a dry run of the late start:
 *   `{type: "task_started", task_id, at: action.at + delay in seconds, dry_run: true}`. The choices are
 *   shown for one action at a time (each name is unique on the page while they are open).
 * - The answer of a dry run is shown in one container holding the text "Ce qui change" and the buttons
 *   "Confirmer" and "Annuler": the number of moved tasks ("2 tâches déplacées": `changes.length`, the
 *   words "tâche(s) déplacée(s)" right after it), one row per change showing the `task_id` and, in the zone
 *   of the recommendations answer, the start before and the start after, and the French `message` of each
 *   `consequences.messages`. "Confirmer" sends the same event without `dry_run`; "Annuler" sends nothing
 *   and removes the container.
 * - After an event that is not a dry run: the `consequences.messages` are shown on the page, the
 *   recommendations are fetched again (no page reload) and the next event uses the new `plan_id`.
 * - A failed event (already_started, future_event, unsupported_item, ...) shows the API `message` in a
 *   `role="alert"`; the actions of the plan stay as they were and no consequence is shown.
 *
 * Historique (`/historique`): `GET .../journal` (most recent first, shown in that order) and the zone of
 * `GET .../schedule` (`tz`). One row per entry, with the French type (task_started "Lancement",
 * task_finished "Fin", priority_changed "Priorités modifiées", item_used "Objet utilisé",
 * resources_corrected "Ressources corrigées", schedule_changed "Horaire modifié", game_data_updated
 * "Données de jeu mises à jour", snapshot_imported "Nouvel import"), the local date and time of `at`, the
 * `task_id` when there is one, and when `plan_id` is not null a link (`a[href]`) whose text is "Plan N"
 * and whose href holds N. The raw type code is never shown.
 *
 * Tableau de bord (`/`): from `GET .../plans/latest` (no plan: nothing shown). Three indicators, each one
 * a container holding its label, its value and the badge "Estimé" (and no other indicator): "Tâches
 * terminées" (assignments whose `end` has passed), "Tâches restantes" (the others) and "Date estimée
 * d'achèvement du plan" (the latest `end`, local date and time of the plan `timezone`). When the plan has
 * `stale` true the text "Nouvel import : plan à recalculer" is shown next to a button that sends
 * `POST .../plans/{plan_id}/events` with `{type: "snapshot_imported"}`; once the plan is no longer stale
 * the text goes away.
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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
      return json(404, { detail: { code: "not_found", message: "Introuvable." } });
    }
    return json(reply.status ?? 200, reply.body);
  };
  vi.stubGlobal("fetch", (input: unknown, init?: unknown) => router.dispatch(input, init));
}

function callsTo(method: string, path: string): Call[] {
  return calls.filter((c) => c.method === method && c.path === path);
}

function eventCalls(): Call[] {
  return calls.filter((c) => c.method === "POST" && /\/plans\/\d+\/events$/.test(c.path));
}

function sentBody(call: Call | undefined): Record<string, unknown> {
  if (call === undefined) {
    throw new Error("the call was not made");
  }
  return call.body === "" ? {} : (JSON.parse(call.body) as Record<string, unknown>);
}

// -------------------------------------------------------------------------------------- fixtures

const TAG = "P2Y8QQ0";
const SCHEDULE = `/api/villages/${TAG}/schedule`;
const TO_EPOCH = `${SCHEDULE}/to-epoch`;
const PLANS = `/api/villages/${TAG}/plans`;
const LATEST = `${PLANS}/latest`;
const NOW_PATH = `/api/villages/${TAG}/recommendations/now`;
const JOURNAL = `/api/villages/${TAG}/journal`;
const ZONE = "Asia/Kolkata"; // UTC+5:30, no daylight saving time
const NOW = Date.UTC(2026, 10, 12, 12, 0, 0) / 1000; // 17:30 on 12/11/2026 in ZONE
const AT = NOW - 1_800; // planned start of the actions, not the real time
const EPOCH = NOW - 777; // what the stub of /to-epoch answers

const HEURISTIC = { kind: "heuristic", objective: 10, bound: null, gap_abs: null, scope: "" };
const PACK = { pack_id: "pack-alpha", game_version: "7.1-test", fictional: false };

const explained = (code: string, message: string) => ({ code, message, value: null });
const RELIABILITY = { level: "high", factors: [explained("confirmed", "Données confirmées.")] };

function action(overrides: Record<string, unknown>) {
  return {
    task_id: "t-1",
    name: "Canon d'essai",
    action: "start",
    at: AT,
    end: AT + 3_600,
    lane: "builder",
    lane_slot: 0,
    why: { reasons: [explained("best_ratio", "Meilleur rapport valeur sur durée.")] },
    advantages: [],
    drawbacks: [],
    reliability: RELIABILITY,
    counterfactual: null,
    ...overrides,
  };
}

const ACTIONS = [
  action({}),
  action({ task_id: "t-2", name: "Mortier d'essai" }),
  action({
    task_id: null,
    name: "Attendre la fin de l'amélioration",
    action: "wait",
    at: NOW,
    end: null,
    lane: null,
    lane_slot: null,
  }),
];
const NEW_ACTIONS = [action({ task_id: "t-3", name: "Cuve d'essai", at: NOW + 600 })];

function reco(overrides: Record<string, unknown> = {}) {
  return {
    plan_id: 7,
    now: NOW,
    timezone: ZONE,
    fictional: false,
    pack: PACK,
    proof: HEURISTIC,
    actions: ACTIONS,
    next_login_at: AT,
    ...overrides,
  };
}

const MSG_LATER = "Le but final est repoussé de 2 h.";
const MSG_IDLE = "Un constructeur reste inactif 10 min de plus.";

const T_BEFORE_1 = Date.UTC(2026, 10, 12, 22, 45, 0) / 1000; // 04:15 on 13/11/2026 in ZONE
const T_AFTER_1 = Date.UTC(2026, 10, 13, 22, 45, 0) / 1000; // 04:15 on 14/11/2026 in ZONE
const T_BEFORE_2 = Date.UTC(2026, 10, 12, 12, 0, 0) / 1000; // 17:30 on 12/11/2026 in ZONE
const T_AFTER_2 = Date.UTC(2026, 10, 12, 13, 30, 0) / 1000; // 19:00 on 12/11/2026 in ZONE

const placement = (start: number) => ({ start, end: start + 3_600, lane: "builder", lane_slot: 0 });

function report(overrides: Record<string, unknown> = {}) {
  return {
    plan_id: 9,
    previous_plan_id: 7,
    dry_run: false,
    changes: [
      { task_id: "t-5", before: placement(T_BEFORE_1), after: placement(T_AFTER_1) },
      { task_id: "t-6", before: placement(T_BEFORE_2), after: placement(T_AFTER_2) },
    ],
    unchanged_count: 3,
    consequences: {
      goal_finish_delta_s: 7_200,
      score_delta: -1.5,
      idle_delta_s: 600,
      messages: [explained("goal_later", MSG_LATER), explained("idle_up", MSG_IDLE)],
    },
    proof: HEURISTIC,
    input_hash: "i".repeat(64),
    ...overrides,
  };
}

type EventError = { status: number; code: string; message: string };

/** The village of the Now page: the plan 7 until an event is recorded, then the plan 9. */
function nowWorld(failure?: EventError): Handler {
  const state = { planId: 7 };
  return (call) => {
    if (call.method === "GET" && call.path === NOW_PATH) {
      return {
        body: state.planId === 7 ? reco() : reco({ plan_id: state.planId, actions: NEW_ACTIONS }),
      };
    }
    if (call.method === "POST" && call.path === TO_EPOCH) {
      return { body: { epoch: EPOCH } };
    }
    const found = /\/plans\/(\d+)\/events$/.exec(call.path);
    if (call.method === "POST" && found !== null) {
      if (Number(found[1]) !== state.planId) {
        return {
          status: 409,
          body: { detail: { code: "plan_outdated", message: "Plan remplacé." } },
        };
      }
      if (failure !== undefined) {
        return {
          status: failure.status,
          body: { detail: { code: failure.code, message: failure.message } },
        };
      }
      if (sentBody(call).dry_run === true) {
        return { body: report({ plan_id: null, dry_run: true }) };
      }
      state.planId = 9;
      return { body: report() };
    }
    return undefined;
  };
}

// ----------------------------------------------------------------------------------- helpers

type User = ReturnType<typeof userEvent.setup>;

function open(route: string): void {
  localStorage.setItem("clashpilot.village", TAG);
  window.history.pushState({}, "", route);
}

function rowOf(element: HTMLElement): HTMLElement {
  const row = element.closest<HTMLElement>("li, tr, article, [role='row'], [role='listitem']");
  if (row === null) {
    throw new Error(`"${element.textContent}" is not inside a row (li, tr, article, role row)`);
  }
  return row;
}

async function rowWith(
  text: string,
  scope: HTMLElement | undefined = undefined,
): Promise<HTMLElement> {
  const [first] = scope
    ? await within(scope).findAllByText(text)
    : await screen.findAllByText(text);
  if (first === undefined) {
    throw new Error(`no element with the text "${text}"`);
  }
  return rowOf(first);
}

function bodyText(): string {
  return document.body.textContent ?? "";
}

/** The text of a container read node by node, so that glued sibling cells stay apart. */
function textOf(node: Node): string {
  const parts: string[] = [];
  const walker = document.createTreeWalker(node, NodeFilter.SHOW_TEXT);
  for (let text = walker.nextNode(); text !== null; text = walker.nextNode()) {
    parts.push(text.textContent ?? "");
  }
  return parts.join(" ").replace(/\s+/g, " ").trim();
}

/** The nearest ancestor of `element` (itself included) that satisfies `accept`. */
function ancestorWhere(element: HTMLElement, accept: (node: HTMLElement) => boolean): HTMLElement {
  for (let node: HTMLElement | null = element; node !== null; node = node.parentElement) {
    if (accept(node)) {
      return node;
    }
  }
  throw new Error(`no container around "${element.textContent}"`);
}

const buttonNamed = (name: string) => (node: HTMLElement) =>
  within(node).queryAllByRole("button", { name }).length > 0;

async function openLaunch(name: string): Promise<HTMLElement> {
  const row = await rowWith(name);
  fireEvent.click(within(row).getByRole("button", { name: "Je l'ai lancée" }));
  return screen.findByRole("dialog");
}

async function launch(user: User, name: string): Promise<HTMLElement> {
  const row = await rowWith(name);
  await user.click(within(row).getByRole("button", { name: "Je l'ai lancée" }));
  return screen.findByRole("dialog");
}

async function simulate(user: User, name: string, choice: string): Promise<void> {
  const row = await rowWith(name);
  await user.click(within(row).getByRole("button", { name: "Simuler un retard" }));
  await user.click(await screen.findByRole("button", { name: choice }));
}

async function changesPanel(): Promise<HTMLElement> {
  const title = await screen.findByText(/Ce qui change/);
  return ancestorWhere(title, buttonNamed("Confirmer"));
}

const TIME_0415 = /\b0?4\s*[:h]\s*15\b/;

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("lang");
  installFetch(() => undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

// ------------------------------------------------------------------------------- Je l'ai lancée

describe("Je l'ai lancée", () => {
  it("is offered on each start action and not on a wait action", async () => {
    installFetch(nowWorld());
    open("/maintenant");
    render(<App />);

    for (const name of ["Canon d'essai", "Mortier d'essai"]) {
      const row = await rowWith(name);
      expect(within(row).queryAllByRole("button", { name: "Je l'ai lancée" })).toHaveLength(1);
      expect(within(row).queryAllByRole("button", { name: "Simuler un retard" })).toHaveLength(1);
    }
    const wait = await rowWith("Attendre la fin de l'amélioration");
    expect(within(wait).queryAllByRole("button", { name: "Je l'ai lancée" })).toHaveLength(0);
    expect(within(wait).queryAllByRole("button", { name: "Simuler un retard" })).toHaveLength(0);
  });

  it("opens a dialog prefilled with the real time in the zone of the plan, and sends nothing yet", async () => {
    installFetch(nowWorld());
    open("/maintenant");
    render(<App />);

    const dialog = await openLaunch("Canon d'essai");
    const field = within(dialog).getByLabelText("Heure réelle de lancement") as HTMLInputElement;
    expect(field.value).toMatch(/^2026-11-12T17:30(:00)?$/);
    expect(within(dialog).getByRole("button", { name: "Valider" })).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: "Annuler" })).toBeTruthy();
    expect(callsTo("POST", TO_EPOCH)).toHaveLength(0);
    expect(eventCalls()).toHaveLength(0);
  });

  it("converts the typed time on the server, then sends task_started with the epoch it returns", async () => {
    installFetch(nowWorld());
    const user = userEvent.setup();
    open("/maintenant");
    render(<App />);

    const dialog = await launch(user, "Canon d'essai");
    fireEvent.change(within(dialog).getByLabelText("Heure réelle de lancement"), {
      target: { value: "2026-11-12T17:45" },
    });
    await user.click(within(dialog).getByRole("button", { name: "Valider" }));

    await waitFor(() => expect(eventCalls()).toHaveLength(1));
    expect(callsTo("POST", TO_EPOCH)).toHaveLength(1);
    const converted = sentBody(callsTo("POST", TO_EPOCH)[0]);
    expect(Object.keys(converted)).toEqual(["local_datetime"]);
    expect(converted.local_datetime).toMatch(/^2026-11-12T17:45(:00)?$/);

    const [event] = eventCalls();
    expect(event?.path).toBe(`${PLANS}/7/events`);
    const body = sentBody(event);
    expect(body).toMatchObject({ type: "task_started", task_id: "t-1", at: EPOCH });
    expect(body.dry_run ?? false).toBe(false);
    expect(
      Object.keys(body)
        .filter((key) => key !== "dry_run")
        .sort(),
    ).toEqual(["at", "task_id", "type"]);
    expect(calls.indexOf(callsTo("POST", TO_EPOCH)[0] as Call)).toBeLessThan(
      calls.indexOf(event as Call),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("sends the prefilled time when the user does not change it", async () => {
    installFetch(nowWorld());
    const user = userEvent.setup();
    open("/maintenant");
    render(<App />);

    const dialog = await launch(user, "Mortier d'essai");
    await user.click(within(dialog).getByRole("button", { name: "Valider" }));

    await waitFor(() => expect(eventCalls()).toHaveLength(1));
    expect(sentBody(callsTo("POST", TO_EPOCH)[0]).local_datetime).toMatch(
      /^2026-11-12T17:30(:00)?$/,
    );
    expect(sentBody(eventCalls()[0])).toMatchObject({ type: "task_started", task_id: "t-2" });
  });

  it("sends nothing when the dialog is cancelled", async () => {
    installFetch(nowWorld());
    const user = userEvent.setup();
    open("/maintenant");
    render(<App />);

    const dialog = await launch(user, "Canon d'essai");
    await user.click(within(dialog).getByRole("button", { name: "Annuler" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(callsTo("POST", TO_EPOCH)).toHaveLength(0);
    expect(eventCalls()).toHaveLength(0);
    expect(calls.filter((c) => c.method !== "GET")).toHaveLength(0);
  });

  it("shows the consequences and the new plan without reload, and replans on the new plan next", async () => {
    installFetch(nowWorld());
    const user = userEvent.setup();
    open("/maintenant");
    render(<App />);

    const dialog = await launch(user, "Canon d'essai");
    await user.click(within(dialog).getByRole("button", { name: "Valider" }));

    await waitFor(() => expect(bodyText()).toContain(MSG_LATER));
    expect(bodyText()).toContain(MSG_IDLE);
    await screen.findByText("Cuve d'essai");
    expect(bodyText()).not.toContain("Canon d'essai");
    expect(bodyText()).not.toContain("Mortier d'essai");
    expect(callsTo("GET", NOW_PATH).length).toBeGreaterThanOrEqual(2);
    expect(window.location.pathname).toBe("/maintenant");

    await simulate(user, "Cuve d'essai", "15 min");
    await waitFor(() => expect(eventCalls()).toHaveLength(2));
    expect(eventCalls()[1]?.path).toBe(`${PLANS}/9/events`);
    expect(sentBody(eventCalls()[1])).toMatchObject({
      type: "task_started",
      task_id: "t-3",
      at: NOW + 600 + 900,
      dry_run: true,
    });
  });

  it.each([
    ["already_started", 409, "Cette amélioration est déjà lancée."],
    ["future_event", 422, "Un événement ne peut pas être daté dans le futur."],
    ["unsupported_item", 422, "Cet objet n'est pas pris en charge."],
  ])(
    "shows the API message of %s in an alert and leaves the plan as it was",
    async (code, status, message) => {
      installFetch(nowWorld({ status, code, message }));
      const user = userEvent.setup();
      open("/maintenant");
      render(<App />);

      const dialog = await launch(user, "Canon d'essai");
      await user.click(within(dialog).getByRole("button", { name: "Valider" }));

      await waitFor(() => expect(eventCalls()).toHaveLength(1));
      await waitFor(async () => {
        const alerts = await screen.findAllByRole("alert", { hidden: true });
        expect(alerts.some((alert) => (alert.textContent ?? "").includes(message))).toBe(true);
      });
      expect(bodyText()).toContain("Canon d'essai");
      expect(bodyText()).toContain("Mortier d'essai");
      expect(bodyText()).toContain("Attendre la fin de l'amélioration");
      expect(bodyText()).not.toContain("Cuve d'essai");
      expect(bodyText()).not.toContain(MSG_LATER);
    },
  );
});

// --------------------------------------------------------------------------- Simuler un retard

describe("Simuler un retard", () => {
  it.each([
    ["15 min", 900],
    ["1 h", 3_600],
  ])("sends a dry run of the late start for %s", async (choice, seconds) => {
    installFetch(nowWorld());
    const user = userEvent.setup();
    open("/maintenant");
    render(<App />);

    await simulate(user, "Canon d'essai", choice);

    await waitFor(() => expect(eventCalls()).toHaveLength(1));
    const [event] = eventCalls();
    expect(event?.path).toBe(`${PLANS}/7/events`);
    expect(sentBody(event)).toEqual({
      type: "task_started",
      task_id: "t-1",
      at: AT + seconds,
      dry_run: true,
    });
    expect(callsTo("POST", TO_EPOCH)).toHaveLength(0);
  });

  it("sends a dry run for a free duration in minutes", async () => {
    installFetch(nowWorld());
    const user = userEvent.setup();
    open("/maintenant");
    render(<App />);

    const row = await rowWith("Mortier d'essai");
    await user.click(within(row).getByRole("button", { name: "Simuler un retard" }));
    fireEvent.change(await screen.findByLabelText("Durée libre (minutes)"), {
      target: { value: "45" },
    });
    await user.click(screen.getByRole("button", { name: "Simuler" }));

    await waitFor(() => expect(eventCalls()).toHaveLength(1));
    expect(sentBody(eventCalls()[0])).toEqual({
      type: "task_started",
      task_id: "t-2",
      at: AT + 2_700,
      dry_run: true,
    });
  });

  it("shows Ce qui change with the count, each move before and after, and the impact", async () => {
    installFetch(nowWorld());
    const user = userEvent.setup();
    open("/maintenant");
    render(<App />);

    await simulate(user, "Canon d'essai", "15 min");

    const panel = await changesPanel();
    await waitFor(() => expect(textOf(panel)).toMatch(/\b2\s+tâches?\s+déplacées?/));
    expect(textOf(panel)).toContain(MSG_LATER);
    expect(textOf(panel)).toContain(MSG_IDLE);

    const first = textOf(await rowWith("t-5", panel));
    expect(first).toContain("13/11/2026");
    expect(first).toContain("14/11/2026");
    expect(first).toMatch(TIME_0415);
    expect(first).not.toMatch(/\b22\s*[:h]\s*45\b/);

    const second = textOf(await rowWith("t-6", panel));
    expect(second).toContain("12/11/2026");
    expect(second).toMatch(/\b17\s*[:h]\s*30\b/);
    expect(second).toMatch(/\b19\s*[:h]\s*00\b/);
    expect(second).not.toMatch(/\b13\s*[:h]\s*30\b/);

    // a simulation records nothing and does not touch the displayed plan
    expect(eventCalls().every((call) => sentBody(call).dry_run === true)).toBe(true);
    expect(bodyText()).toContain("Canon d'essai");
    expect(bodyText()).not.toContain("Cuve d'essai");
  });

  it("sends the same event without dry_run when the simulation is confirmed", async () => {
    installFetch(nowWorld());
    const user = userEvent.setup();
    open("/maintenant");
    render(<App />);

    await simulate(user, "Canon d'essai", "1 h");
    const panel = await changesPanel();
    await user.click(within(panel).getByRole("button", { name: "Confirmer" }));

    await waitFor(() => expect(eventCalls()).toHaveLength(2));
    const [dry, real] = eventCalls().map(sentBody);
    expect(dry?.dry_run).toBe(true);
    expect(real?.dry_run ?? false).toBe(false);
    const { dry_run: _dry, ...dryEvent } = dry ?? {};
    const { dry_run: _real, ...realEvent } = real ?? {};
    expect(realEvent).toEqual(dryEvent);
    expect(realEvent).toEqual({ type: "task_started", task_id: "t-1", at: AT + 3_600 });
    expect(eventCalls()[1]?.path).toBe(`${PLANS}/7/events`);

    await screen.findByText("Cuve d'essai");
    expect(bodyText()).toContain(MSG_LATER);
    expect(bodyText()).not.toContain("Mortier d'essai");
  });

  it("sends no further request when the simulation is cancelled", async () => {
    installFetch(nowWorld());
    const user = userEvent.setup();
    open("/maintenant");
    render(<App />);

    await simulate(user, "Canon d'essai", "15 min");
    const panel = await changesPanel();
    await user.click(within(panel).getByRole("button", { name: "Annuler" }));

    await waitFor(() => expect(screen.queryByText(/Ce qui change/)).toBeNull());
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(eventCalls()).toHaveLength(1);
    expect(sentBody(eventCalls()[0]).dry_run).toBe(true);
    expect(bodyText()).toContain("Canon d'essai");
    expect(bodyText()).not.toContain("Cuve d'essai");
  });

  it("shows the API message in an alert when the simulation is refused", async () => {
    const message = "Un événement ne peut pas être daté dans le futur.";
    installFetch(nowWorld({ status: 422, code: "future_event", message }));
    const user = userEvent.setup();
    open("/maintenant");
    render(<App />);

    await simulate(user, "Canon d'essai", "1 h");

    await waitFor(async () => {
      const alerts = await screen.findAllByRole("alert", { hidden: true });
      expect(alerts.some((alert) => (alert.textContent ?? "").includes(message))).toBe(true);
    });
    expect(screen.queryByText(/Ce qui change/)).toBeNull();
    expect(bodyText()).toContain("Canon d'essai");
    expect(bodyText()).toContain("Mortier d'essai");
  });
});

// --------------------------------------------------------------------------------- Historique

const JOURNAL_ENTRIES = [
  {
    entry_id: 4,
    type: "schedule_changed",
    at: Date.UTC(2026, 10, 15, 9, 0, 0) / 1000,
    task_id: null,
    payload: {},
    plan_id: null,
  },
  {
    entry_id: 3,
    type: "snapshot_imported",
    at: Date.UTC(2026, 10, 14, 9, 0, 0) / 1000,
    task_id: null,
    payload: {},
    plan_id: 9,
  },
  {
    entry_id: 2,
    type: "task_started",
    at: T_BEFORE_1, // 04:15 on 13/11/2026 in ZONE
    task_id: "cannon-3",
    payload: {},
    plan_id: 8,
  },
  {
    entry_id: 1,
    type: "priority_changed",
    at: Date.UTC(2026, 10, 11, 9, 0, 0) / 1000,
    task_id: null,
    payload: {},
    plan_id: 7,
  },
];

const PROFILE = {
  tz: ZONE,
  weekly: [],
  exceptions: [],
  policy: { mode: "strict", tolerance_minutes: 0, max_logins_per_day: null },
};

function journalWorld(entries: unknown[] = JOURNAL_ENTRIES): Handler {
  return (call) => {
    if (call.method === "GET" && call.path === JOURNAL) {
      return { body: entries };
    }
    if (call.method === "GET" && call.path === SCHEDULE) {
      return { body: PROFILE };
    }
    if (call.method === "GET" && call.path === LATEST) {
      return { body: plan() };
    }
    if (call.method === "GET" && call.path === NOW_PATH) {
      return { body: reco() };
    }
    return undefined;
  };
}

describe("Historique", () => {
  it("lists the journal rows from the most recent to the oldest, with a French type", async () => {
    installFetch(journalWorld());
    open("/historique");
    render(<App />);

    const labels = ["Horaire modifié", "Nouvel import", "Lancement", "Priorités modifiées"];
    const rows: HTMLElement[] = [];
    for (const label of labels) {
      rows.push(await rowWith(label));
    }
    for (let index = 1; index < rows.length; index += 1) {
      const previous = rows[index - 1] as HTMLElement;
      const current = rows[index] as HTMLElement;
      expect(previous).not.toBe(current);
      expect(
        previous.compareDocumentPosition(current) & Node.DOCUMENT_POSITION_FOLLOWING,
        `${labels[index - 1]} comes before ${labels[index]}`,
      ).toBeTruthy();
    }
    for (const code of [
      "schedule_changed",
      "snapshot_imported",
      "task_started",
      "priority_changed",
    ]) {
      expect(bodyText()).not.toContain(code);
    }
  });

  it.each([
    ["task_finished", "Fin"],
    ["item_used", "Objet utilisé"],
    ["resources_corrected", "Ressources corrigées"],
    ["game_data_updated", "Données de jeu mises à jour"],
  ])("says %s as %s", async (type, label) => {
    installFetch(
      journalWorld([{ entry_id: 1, type, at: NOW, task_id: null, payload: {}, plan_id: null }]),
    );
    open("/historique");
    render(<App />);

    expect(await rowWith(label)).toBeTruthy();
    expect(bodyText()).not.toContain(type);
  });

  it("shows the local date and time, the task and a link to the plan produced", async () => {
    installFetch(journalWorld());
    open("/historique");
    render(<App />);

    const started = await rowWith("Lancement");
    const text = textOf(started);
    expect(text).toContain("13/11/2026");
    expect(text).toMatch(TIME_0415);
    expect(text).not.toMatch(/\b22\s*[:h]\s*45\b/);
    expect(text).toContain("cannon-3");
    const link = within(started).getByRole("link", { name: /Plan\s*8\b/ });
    expect(link.getAttribute("href") ?? "").toContain("8");

    const imported = await rowWith("Nouvel import");
    expect(within(imported).getByRole("link", { name: /Plan\s*9\b/ })).toBeTruthy();
  });

  it("shows no link on an entry without a plan", async () => {
    installFetch(journalWorld());
    open("/historique");
    render(<App />);

    const changed = await rowWith("Horaire modifié");
    expect(within(changed).queryAllByRole("link")).toHaveLength(0);
  });
});

// ------------------------------------------------------------------------------------ Tableau de bord

const assignment = (id: string, status: string, start: number, end: number) => ({
  task_id: id,
  data_id: 1,
  name: `Tâche ${id}`,
  lane: "builder",
  lane_slot: 0,
  start,
  end,
  status,
  requires: [],
});

const PAST = 1_000_000_000; // 2001
const FUTURE = Date.UTC(2100, 2, 10, 0, 0, 0) / 1000;
const LAST_END = Date.UTC(2100, 2, 10, 22, 45, 0) / 1000; // 04:15 on 11/03/2100 in ZONE

function plan(overrides: Record<string, unknown> = {}) {
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
    assignments: [
      assignment("a-1", "frozen", PAST, PAST + 3_600),
      assignment("a-2", "frozen", PAST + 7_200, PAST + 10_800),
      assignment("a-3", "planned", FUTURE, FUTURE + 3_600),
      assignment("a-4", "planned", FUTURE + 7_200, FUTURE + 14_400),
      assignment("a-5", "planned", LAST_END - 3_600, LAST_END),
    ],
    excluded: [],
    warnings: [],
    stale: false,
    ...overrides,
  };
}

function dashboardWorld(options: { stale?: boolean } = {}): Handler {
  const state = { stale: options.stale === true, planId: 8 };
  return (call) => {
    if (call.method === "GET" && call.path === LATEST) {
      return { body: plan({ plan_id: state.planId, stale: state.stale }) };
    }
    if (call.method === "GET" && call.path === "/api/gamedata/packs") {
      return { body: [] };
    }
    const found = /\/plans\/(\d+)\/events$/.exec(call.path);
    if (call.method === "POST" && found !== null) {
      state.stale = false;
      state.planId = 10;
      return { body: report({ plan_id: 10, previous_plan_id: 8 }) };
    }
    return undefined;
  };
}

const INDICATORS = [
  /^Tâches terminées$/,
  /^Tâches restantes$/,
  /^Date estimée d'achèvement du plan$/,
];

/** The container of one indicator: its label, value and Estimé badge, and no other indicator. */
async function indicator(index: number): Promise<string> {
  const label = await screen.findByText(INDICATORS[index] as RegExp);
  const others = INDICATORS.filter((_, position) => position !== index);
  for (let node: HTMLElement | null = label; node !== null; node = node.parentElement) {
    const text = textOf(node);
    if (others.some((other) => new RegExp(other.source.replace(/^\^|\$$/g, "")).test(text))) {
      break;
    }
    if (text.includes("Estimé")) {
      return text;
    }
  }
  throw new Error(`the indicator ${INDICATORS[index]} has no container with an Estimé badge`);
}

describe("Tableau de bord", () => {
  it("shows the finished and remaining tasks and the estimated end date, each marked Estimé", async () => {
    installFetch(dashboardWorld());
    open("/");
    render(<App />);

    const finished = await indicator(0);
    expect(finished.replace("Tâches terminées", "")).toMatch(/\b2\b/);
    expect(finished).not.toMatch(/\b3\b/);

    const remaining = await indicator(1);
    expect(remaining.replace("Tâches restantes", "")).toMatch(/\b3\b/);
    expect(remaining).not.toMatch(/\b2\b/);

    const end = await indicator(2);
    expect(end).toContain("11/03/2100");
    expect(end).toMatch(TIME_0415);
    expect(end).not.toMatch(/\b22\s*[:h]\s*45\b/);
    expect(end).not.toContain("10/03/2100");
  });

  it("shows no stale banner when the plan is up to date", async () => {
    installFetch(dashboardWorld());
    open("/");
    render(<App />);

    await indicator(1);
    expect(screen.queryByText(/Nouvel import : plan à recalculer/)).toBeNull();
    expect(eventCalls()).toHaveLength(0);
  });

  it("shows the Nouvel import banner for a stale plan and replans with snapshot_imported", async () => {
    installFetch(dashboardWorld({ stale: true }));
    const user = userEvent.setup();
    open("/");
    render(<App />);

    const text = await screen.findByText(/Nouvel import : plan à recalculer/);
    const banner = ancestorWhere(text, (node) => within(node).queryAllByRole("button").length > 0);
    expect(eventCalls()).toHaveLength(0);
    await user.click(within(banner).getAllByRole("button")[0] as HTMLElement);

    await waitFor(() => expect(eventCalls()).toHaveLength(1));
    const [event] = eventCalls();
    expect(event?.path).toBe(`${PLANS}/8/events`);
    const body = sentBody(event);
    expect(body.type).toBe("snapshot_imported");
    expect(body.dry_run ?? false).toBe(false);
    await waitFor(() => expect(screen.queryByText(/Nouvel import : plan à recalculer/)).toBeNull());
  });

  it("shows no indicator and no banner when there is no plan yet", async () => {
    installFetch((call) => {
      if (call.method === "GET" && call.path === LATEST) {
        return {
          status: 404,
          body: { detail: { code: "no_plan", message: "Aucun plan n'a encore été calculé." } },
        };
      }
      return call.path === "/api/gamedata/packs" ? { body: [] } : undefined;
    });
    open("/");
    render(<App />);

    await waitFor(() => expect(callsTo("GET", LATEST).length).toBeGreaterThan(0));
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(screen.queryByText(INDICATORS[0] as RegExp)).toBeNull();
    expect(screen.queryByText(/Nouvel import : plan à recalculer/)).toBeNull();
  });
});
