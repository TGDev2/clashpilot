/**
 * E13 acceptance tests: schedule settings and "Que faire maintenant ?" (frozen with the exam commit).
 *
 * Contract of the UI these tests drive (everything is French, labels come from `fr.ts`):
 * - The current village is the one kept by the app in `localStorage["clashpilot.village"]`; the tests
 *   set it, render `App` at the route and drive the page. Server errors are `{detail: {code, message}}`.
 *
 * Réglages (`/reglages`): on load `GET /api/villages/{id}/schedule` (404 `schedule_not_found` = empty form).
 * - A text input "Fuseau horaire" (IANA key, sent as `tz`) and a radio group "Politique de connexion" with
 *   the radios "Interdire les fins pendant le sommeil" (`strict`), "Tolérer une inactivité limitée"
 *   (`tolerance`), "Priorité à la progression" (`progress`), "Minimiser les connexions" (`min_logins`).
 *   The input "Durée de tolérance (minutes)" (`policy.tolerance_minutes`, integer) exists only while
 *   `tolerance` is chosen; "Nombre maximal de connexions par jour" (`policy.max_logins_per_day`) only while
 *   `min_logins` is chosen. A policy field that does not apply is sent as 0 / null.
 * - Four `group`s named "Fenêtres de sommeil", "Fenêtres de travail", "Fenêtres de disponibilité" and
 *   "Plages préférées" (kinds sleep, work, available, preferred). In each one the button "Ajouter une
 *   fenêtre" appends a row with the labels "Jour" (a native select: "Tous les jours" = null, then "Lundi"
 *   ... "Dimanche" = 0..6), "Début" and "Fin" (text inputs, `HH:MM`).
 * - A `group` "Absences" with the button "Ajouter une absence"; each row has the text inputs "Début de
 *   l'absence" and "Fin de l'absence" (`AAAA-MM-JJTHH:MM:SS`, local time, sent as `start_local` / `end_local`
 *   of an exception of kind `absence`).
 * - The button "Enregistrer l'horaire" sends `PUT /api/villages/{id}/schedule` with `{tz, weekly,
 *   exceptions, policy}` (the ScheduleProfile of the API).
 *
 * Que faire maintenant ? (`/maintenant`): on load `GET .../recommendations/now` (404 `no_plan` = nothing to
 * show, the button stays).
 * - The button "Calculer mon plan" (its name never changes) sends `POST .../plans` with a body holding
 *   neither `engine` nor `profile`, is disabled while the call runs, and an element carrying an
 *   `aria-live` attribute (present from the first render) announces the progress with the word "calcul".
 * - After a plan answer with `reproducible: false`, the text "Résultat rapide, non reproductible" is shown
 *   within two ancestors of the proof label; with `reproducible: true` the text is absent.
 * - Each action (`li`, `tr`, `article`, role row/listitem) shows its name, the messages of `why.reasons`,
 *   `advantages` and `drawbacks`, the words "Avantages", "Inconvénients", "Fiabilité", the level
 *   (high "Élevée", medium "Moyenne", low "Faible") and the messages of the reliability factors.
 * - A `wait` action shows its `at` as local time, computed with `Intl.DateTimeFormat` and the `timezone` of
 *   the answer (`timeZone` option), never the UTC time.
 * - The proof label (one component): `heuristic` "Solution heuristique, non prouvée optimale"; `feasible`
 *   "Solution réalisable, écart annoncé" then `gap_abs`, or "Solution réalisable, écart non calculé" when
 *   `gap_abs` is null; `optimal_window` "Optimale sur la fenêtre" then the `scope`. The word "optimale"
 *   appears nowhere else for heuristic and feasible.
 * - A recommendations answer with `fictional: true` shows the banner "Données fictives".
 * - A 409 `missing_input` (detail.missing = field names) is shown in a `role="alert"` region listing each
 *   field name; the alert holds a link to `/importer#saisie` when a field other than `schedule_profile` is
 *   missing, and a link to `/reglages` when `schedule_profile` is missing.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
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
      const missing = call.path === `${SCHEDULE}` ? "schedule_not_found" : "no_plan";
      return json(404, { detail: { code: missing, message: "Introuvable." } });
    }
    return json(reply.status ?? 200, reply.body);
  };
  vi.stubGlobal("fetch", (input: unknown, init?: unknown) => router.dispatch(input, init));
}

function callsTo(method: string, path: string): Call[] {
  return calls.filter((c) => c.method === method && c.path === path);
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
const PLANS = `/api/villages/${TAG}/plans`;
const NOW_PATH = `/api/villages/${TAG}/recommendations/now`;
const NOW = Date.UTC(2026, 10, 12, 12, 0, 0) / 1000;
const WAIT_AT = Date.UTC(2026, 10, 12, 22, 45, 0) / 1000; // 04:15 the next day in Asia/Kolkata
const ZONE = "Asia/Kolkata";

type Explained = { code: string; message: string; value: unknown };
const explained = (code: string, message: string): Explained => ({ code, message, value: null });

type Proof = {
  kind: "heuristic" | "feasible" | "optimal_window";
  objective: number;
  bound: number | null;
  gap_abs: number | null;
  scope: string;
};
const HEURISTIC: Proof = {
  kind: "heuristic",
  objective: 10,
  bound: null,
  gap_abs: null,
  scope: "",
};

const PACK = { pack_id: "pack-alpha", game_version: "7.1-test", fictional: false };

function action(overrides: Record<string, unknown>) {
  return {
    task_id: "t-1",
    name: "Canon d'essai",
    action: "start",
    at: NOW,
    end: NOW + 3_600,
    lane: "builder",
    lane_slot: 0,
    why: { reasons: [explained("best_ratio", "Meilleur rapport valeur sur durée.")] },
    advantages: [explained("before_night", "Se termine avant votre nuit.")],
    drawbacks: [explained("uses_gold", "Consomme tout l'or disponible.")],
    reliability: {
      level: "high",
      factors: [explained("confirmed", "Toutes les données sont confirmées.")],
    },
    counterfactual: null,
    ...overrides,
  };
}

const ACTIONS = [
  action({}),
  action({
    task_id: "t-2",
    name: "Mortier d'essai",
    why: { reasons: [explained("unlocks", "Débloque une amélioration suivante.")] },
    advantages: [explained("cheap", "Coût faible en élixir.")],
    drawbacks: [explained("long", "Occupe un constructeur longtemps.")],
    reliability: {
      level: "medium",
      factors: [explained("stock_estimated", "Le stock d'élixir est estimé.")],
    },
  }),
  action({
    task_id: null,
    name: "Attendre la fin de l'amélioration",
    action: "wait",
    at: WAIT_AT,
    end: null,
    lane: null,
    lane_slot: null,
    why: { reasons: [explained("no_resource", "Aucune ressource suffisante maintenant.")] },
    advantages: [explained("saves_gems", "Aucune gemme dépensée.")],
    drawbacks: [explained("idle", "Un constructeur reste inactif.")],
    reliability: {
      level: "low",
      factors: [explained("no_income", "Le revenu horaire n'est pas renseigné.")],
    },
  }),
];

function reco(overrides: Record<string, unknown> = {}) {
  return {
    plan_id: 7,
    now: NOW,
    timezone: ZONE,
    fictional: false,
    pack: PACK,
    proof: HEURISTIC,
    actions: ACTIONS,
    next_login_at: NOW,
    ...overrides,
  };
}

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
    assignments: [],
    excluded: [],
    warnings: [],
    stale: false,
    ...overrides,
  };
}

function nowHandlers(overrides: Record<string, unknown> = {}, planBody = plan()): Handler {
  return (call) => {
    if (call.method === "GET" && call.path === NOW_PATH) {
      return { body: reco(overrides) };
    }
    if (call.method === "POST" && call.path === PLANS) {
      return { status: 201, body: planBody };
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

async function actionRow(name: string): Promise<HTMLElement> {
  const [first] = await screen.findAllByText(name);
  if (first === undefined) {
    throw new Error(`no element with the text "${name}"`);
  }
  return rowOf(first);
}

function bodyText(): string {
  return document.body.textContent ?? "";
}

async function fill(user: User, field: HTMLElement, value: string): Promise<void> {
  await user.clear(field);
  await user.type(field, value);
}

async function addRow(user: User, group: string, button: string): Promise<HTMLElement> {
  const region = screen.getByRole("group", { name: group });
  await user.click(within(region).getByRole("button", { name: button }));
  return region;
}

async function fillWindow(
  user: User,
  group: string,
  weekday: string,
  start: string,
  end: string,
): Promise<void> {
  const region = await addRow(user, group, "Ajouter une fenêtre");
  await user.selectOptions(within(region).getByLabelText("Jour"), weekday);
  await fill(user, within(region).getByLabelText("Début"), start);
  await fill(user, within(region).getByLabelText("Fin"), end);
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

// -------------------------------------------------------------------------------- settings page

const STORED_SCHEDULE = {
  tz: "Europe/Paris",
  weekly: [],
  exceptions: [],
  policy: { mode: "strict", tolerance_minutes: 0, max_logins_per_day: null },
};

describe("settings page", () => {
  it("sends PUT /schedule with the timezone, every kind of window, the absence and the policy", async () => {
    installFetch((call) =>
      call.method === "PUT" && call.path === SCHEDULE
        ? { body: { ...STORED_SCHEDULE, tz: "Europe/Paris" } }
        : undefined,
    );
    const user = userEvent.setup();
    open("/reglages");
    render(<App />);

    await fill(user, await screen.findByLabelText("Fuseau horaire"), "Europe/Paris");
    await fillWindow(user, "Fenêtres de sommeil", "Tous les jours", "23:00", "07:00");
    await fillWindow(user, "Fenêtres de travail", "Lundi", "09:00", "17:00");
    await fillWindow(user, "Fenêtres de disponibilité", "Mardi", "18:30", "22:00");
    await fillWindow(user, "Plages préférées", "Samedi", "10:00", "12:00");
    const absences = await addRow(user, "Absences", "Ajouter une absence");
    await fill(user, within(absences).getByLabelText("Début de l'absence"), "2026-12-24T08:00:00");
    await fill(user, within(absences).getByLabelText("Fin de l'absence"), "2026-12-26T20:00:00");
    await user.click(screen.getByRole("radio", { name: "Tolérer une inactivité limitée" }));
    await fill(user, screen.getByLabelText(/Durée de tolérance/), "90");
    await user.click(screen.getByRole("button", { name: "Enregistrer l'horaire" }));

    await waitFor(() => expect(callsTo("PUT", SCHEDULE)).toHaveLength(1));
    const body = sentBody(callsTo("PUT", SCHEDULE)[0]);
    expect(body.tz).toBe("Europe/Paris");
    const weekly = body.weekly as Array<Record<string, unknown>>;
    expect(weekly).toHaveLength(4);
    expect(weekly).toEqual(
      expect.arrayContaining([
        { weekday: null, start: "23:00", end: "07:00", kind: "sleep" },
        { weekday: 0, start: "09:00", end: "17:00", kind: "work" },
        { weekday: 1, start: "18:30", end: "22:00", kind: "available" },
        { weekday: 5, start: "10:00", end: "12:00", kind: "preferred" },
      ]),
    );
    expect(body.exceptions).toEqual([
      { start_local: "2026-12-24T08:00:00", end_local: "2026-12-26T20:00:00", kind: "absence" },
    ]);
    expect(body.policy).toMatchObject({ mode: "tolerance", tolerance_minutes: 90 });
    expect(Object.keys(body).sort()).toEqual(["exceptions", "policy", "tz", "weekly"]);
  });

  it.each([
    ["Interdire les fins pendant le sommeil", "strict"],
    ["Priorité à la progression", "progress"],
  ])("offers the policy %s and sends the mode %s", async (label, mode) => {
    installFetch((call) =>
      call.method === "PUT" && call.path === SCHEDULE ? { body: STORED_SCHEDULE } : undefined,
    );
    const user = userEvent.setup();
    open("/reglages");
    render(<App />);

    await fill(user, await screen.findByLabelText("Fuseau horaire"), "Europe/Paris");
    await user.click(screen.getByRole("radio", { name: label }));
    await user.click(screen.getByRole("button", { name: "Enregistrer l'horaire" }));

    await waitFor(() => expect(callsTo("PUT", SCHEDULE)).toHaveLength(1));
    expect(sentBody(callsTo("PUT", SCHEDULE)[0]).policy).toMatchObject({ mode });
  });

  it("sends the number of logins for the policy Minimiser les connexions", async () => {
    installFetch((call) =>
      call.method === "PUT" && call.path === SCHEDULE ? { body: STORED_SCHEDULE } : undefined,
    );
    const user = userEvent.setup();
    open("/reglages");
    render(<App />);

    await fill(user, await screen.findByLabelText("Fuseau horaire"), "Europe/Paris");
    await user.click(screen.getByRole("radio", { name: "Minimiser les connexions" }));
    await fill(user, screen.getByLabelText(/Nombre maximal de connexions par jour/), "3");
    await user.click(screen.getByRole("button", { name: "Enregistrer l'horaire" }));

    await waitFor(() => expect(callsTo("PUT", SCHEDULE)).toHaveLength(1));
    expect(sentBody(callsTo("PUT", SCHEDULE)[0]).policy).toMatchObject({
      mode: "min_logins",
      max_logins_per_day: 3,
    });
  });

  it("shows the tolerance input only for Tolérer une inactivité limitée", async () => {
    const user = userEvent.setup();
    open("/reglages");
    render(<App />);
    await screen.findByLabelText("Fuseau horaire");

    const tolerance = () => screen.queryByLabelText(/Durée de tolérance/);
    const logins = () => screen.queryByLabelText(/Nombre maximal de connexions par jour/);
    for (const label of [
      "Interdire les fins pendant le sommeil",
      "Priorité à la progression",
      "Minimiser les connexions",
    ]) {
      await user.click(screen.getByRole("radio", { name: label }));
      expect(tolerance(), label).toBeNull();
    }
    await user.click(screen.getByRole("radio", { name: "Tolérer une inactivité limitée" }));
    expect(tolerance()).not.toBeNull();
    expect(logins()).toBeNull();
  });

  it("shows the connections input only for Minimiser les connexions", async () => {
    const user = userEvent.setup();
    open("/reglages");
    render(<App />);
    await screen.findByLabelText("Fuseau horaire");

    const logins = () => screen.queryByLabelText(/Nombre maximal de connexions par jour/);
    for (const label of [
      "Interdire les fins pendant le sommeil",
      "Tolérer une inactivité limitée",
      "Priorité à la progression",
    ]) {
      await user.click(screen.getByRole("radio", { name: label }));
      expect(logins(), label).toBeNull();
    }
    await user.click(screen.getByRole("radio", { name: "Minimiser les connexions" }));
    expect(logins()).not.toBeNull();
    expect(screen.queryByLabelText(/Durée de tolérance/)).toBeNull();
  });
});

// ------------------------------------------------------------------------- compute button

describe("Calculer mon plan", () => {
  it("posts to /plans without engine nor profile", async () => {
    installFetch(nowHandlers());
    const user = userEvent.setup();
    open("/maintenant");
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Calculer mon plan" }));

    await waitFor(() => expect(callsTo("POST", PLANS)).toHaveLength(1));
    const body = sentBody(callsTo("POST", PLANS)[0]);
    expect(body).not.toHaveProperty("engine");
    expect(body).not.toHaveProperty("profile");
  });

  it("offers the button when no plan exists yet", async () => {
    installFetch(() => undefined);
    open("/maintenant");
    render(<App />);
    expect(await screen.findByRole("button", { name: "Calculer mon plan" })).toBeTruthy();
    await waitFor(() => expect(callsTo("GET", NOW_PATH).length).toBeGreaterThan(0));
  });

  it("is disabled and announced in an aria-live region while the plan is computed", async () => {
    let release: (reply: Reply) => void = () => undefined;
    const pending = new Promise<Reply>((resolve) => {
      release = resolve;
    });
    installFetch((call) => {
      if (call.method === "POST" && call.path === PLANS) {
        return pending;
      }
      return nowHandlers()(call);
    });
    const user = userEvent.setup();
    open("/maintenant");
    render(<App />);

    const button = await screen.findByRole("button", { name: "Calculer mon plan" });
    expect(document.querySelector("[aria-live]")).not.toBeNull();
    expect((button as HTMLButtonElement).disabled).toBe(false);
    await user.click(button);

    await waitFor(() =>
      expect(
        (screen.getByRole("button", { name: "Calculer mon plan" }) as HTMLButtonElement).disabled,
      ).toBe(true),
    );
    const live = [...document.querySelectorAll("[aria-live]")];
    expect(live.some((region) => /calcul/i.test(region.textContent ?? ""))).toBe(true);

    release({ status: 201, body: plan() });
    await waitFor(() =>
      expect(
        (screen.getByRole("button", { name: "Calculer mon plan" }) as HTMLButtonElement).disabled,
      ).toBe(false),
    );
  });

  it.each([
    ["false", false, true],
    ["true", true, false],
  ])("handles reproducible %s next to the proof label", async (_name, reproducible, shown) => {
    installFetch(nowHandlers({}, plan({ reproducible })));
    const user = userEvent.setup();
    open("/maintenant");
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Calculer mon plan" }));
    await waitFor(() => expect(callsTo("POST", PLANS)).toHaveLength(1));
    await waitFor(() =>
      expect(
        (screen.getByRole("button", { name: "Calculer mon plan" }) as HTMLButtonElement).disabled,
      ).toBe(false),
    );
    const label = await screen.findByText(/Solution heuristique, non prouvée optimale/);

    const text = "Résultat rapide, non reproductible";
    if (shown) {
      await waitFor(() => expect(bodyText()).toContain(text));
      const near = label.parentElement?.parentElement?.textContent ?? "";
      expect(near).toContain(text);
    } else {
      await new Promise((resolve) => setTimeout(resolve, 50));
      expect(bodyText()).not.toContain(text);
    }
  });
});

// ------------------------------------------------------------------------- recommendations

describe("Que faire maintenant ?", () => {
  it("shows each action with its reasons, advantages, drawbacks and reliability", async () => {
    installFetch(nowHandlers());
    open("/maintenant");
    render(<App />);

    const expected: Array<[string, string[], string, string[]]> = [
      [
        "Canon d'essai",
        [
          "Meilleur rapport valeur sur durée.",
          "Se termine avant votre nuit.",
          "Consomme tout l'or disponible.",
        ],
        "Élevée",
        ["Toutes les données sont confirmées."],
      ],
      [
        "Mortier d'essai",
        [
          "Débloque une amélioration suivante.",
          "Coût faible en élixir.",
          "Occupe un constructeur longtemps.",
        ],
        "Moyenne",
        ["Le stock d'élixir est estimé."],
      ],
      [
        "Attendre la fin de l'amélioration",
        [
          "Aucune ressource suffisante maintenant.",
          "Aucune gemme dépensée.",
          "Un constructeur reste inactif.",
        ],
        "Faible",
        ["Le revenu horaire n'est pas renseigné."],
      ],
    ];
    for (const [name, messages, level, factors] of expected) {
      const text = (await actionRow(name)).textContent ?? "";
      for (const shown of [
        ...messages,
        ...factors,
        "Avantages",
        "Inconvénients",
        "Fiabilité",
        level,
      ]) {
        expect(text, `${name}: ${shown}`).toContain(shown);
      }
      for (const other of ["Élevée", "Moyenne", "Faible"].filter((l) => l !== level)) {
        expect(text, `${name} must not say ${other}`).not.toContain(other);
      }
    }
  });

  it("shows the wait time with Intl.DateTimeFormat and the timezone of the answer", async () => {
    const formats = vi.spyOn(Intl, "DateTimeFormat");
    installFetch(nowHandlers());
    open("/maintenant");
    render(<App />);

    const text = (await actionRow("Attendre la fin de l'amélioration")).textContent ?? "";
    expect(text).toMatch(/\b0?4\s*[:h]\s*15\b/);
    expect(text).not.toMatch(/\b22\s*[:h]\s*45\b/);
    const zones = formats.mock.calls.map(
      ([, options]) => (options as { timeZone?: string })?.timeZone,
    );
    expect(zones).toContain(ZONE);
  });

  describe("proof label", () => {
    async function labelOn(proof: Proof): Promise<string> {
      installFetch(nowHandlers({ proof }));
      open("/maintenant");
      render(<App />);
      await actionRow("Canon d'essai");
      return bodyText();
    }

    it("says Solution heuristique, non prouvée optimale for heuristic, never optimale alone", async () => {
      const text = await labelOn(HEURISTIC);
      expect(text).toContain("Solution heuristique, non prouvée optimale");
      expect(text.replace("non prouvée optimale", "")).not.toMatch(/optimale/i);
    });

    it("says Solution réalisable, écart annoncé followed by gap_abs for feasible", async () => {
      const text = await labelOn({
        kind: "feasible",
        objective: 100,
        bound: 112.5,
        gap_abs: 12.5,
        scope: "fenêtre 3 j, grille 5 min, objectif discrétisé",
      });
      expect(text).toMatch(/Solution réalisable, écart annoncé\s*:?\s*12[.,]5/);
      expect(text).not.toMatch(/optimale/i);
      expect(text).not.toContain("écart non calculé");
    });

    it("says écart non calculé for feasible when gap_abs is null", async () => {
      const text = await labelOn({
        kind: "feasible",
        objective: 100,
        bound: null,
        gap_abs: null,
        scope: "",
      });
      expect(text).toContain("Solution réalisable");
      expect(text).toContain("écart non calculé");
      expect(text).not.toContain("écart annoncé");
      expect(text).not.toMatch(/optimale/i);
    });

    it("says Optimale sur la fenêtre with the scope for optimal_window", async () => {
      const scope = "fenêtre 3 j, grille 5 min, objectif discrétisé";
      const text = await labelOn({
        kind: "optimal_window",
        objective: 100,
        bound: 100,
        gap_abs: 0,
        scope,
      });
      expect(text).toContain("Optimale sur la fenêtre");
      expect(text).toContain(scope);
      expect(text).not.toContain("Solution heuristique");
    });
  });

  it("shows the Données fictives banner for a plan made with a fictional pack", async () => {
    installFetch(nowHandlers({ fictional: true, pack: { ...PACK, fictional: true } }));
    open("/maintenant");
    render(<App />);
    await actionRow("Canon d'essai");
    expect(screen.getAllByText(/Données fictives/).length).toBeGreaterThan(0);
  });

  it("hides the banner for a real pack", async () => {
    installFetch(nowHandlers());
    open("/maintenant");
    render(<App />);
    await actionRow("Canon d'essai");
    expect(screen.queryByText(/Données fictives/)).toBeNull();
  });
});

// --------------------------------------------------------------------------- missing input

describe("missing_input errors", () => {
  async function computeWithMissing(missing: string[]): Promise<HTMLElement> {
    installFetch((call) =>
      call.method === "POST" && call.path === PLANS
        ? {
            status: 409,
            body: {
              detail: {
                code: "missing_input",
                message: `Des données manquent pour calculer le plan : ${missing.join(", ")}.`,
                missing,
              },
            },
          }
        : undefined,
    );
    const user = userEvent.setup();
    open("/maintenant");
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Calculer mon plan" }));
    const alerts = await screen.findAllByRole("alert");
    const alert = alerts.find((a) => (a.textContent ?? "").includes(missing[0] ?? ""));
    if (alert === undefined) {
      throw new Error("no alert lists the missing fields");
    }
    return alert;
  }

  it("lists the fields and links to the manual input", async () => {
    const alert = await computeWithMissing(["builders_total", "gold"]);
    expect(alert.textContent).toContain("builders_total");
    expect(alert.textContent).toContain("gold");
    expect(alert.querySelector('a[href="/importer#saisie"]')).not.toBeNull();
    expect(alert.querySelector('a[href="/reglages"]')).toBeNull();
  });

  it("links to Réglages when schedule_profile is missing", async () => {
    const alert = await computeWithMissing(["schedule_profile"]);
    expect(alert.textContent).toContain("schedule_profile");
    expect(alert.querySelector('a[href="/reglages"]')).not.toBeNull();
    expect(alert.querySelector('a[href="/importer#saisie"]')).toBeNull();
  });

  it("links to both when a field and schedule_profile are missing", async () => {
    const alert = await computeWithMissing(["builders_total", "schedule_profile"]);
    expect(alert.querySelector('a[href="/importer#saisie"]')).not.toBeNull();
    expect(alert.querySelector('a[href="/reglages"]')).not.toBeNull();
  });
});
