/**
 * E20 acceptance tests: strategies, resources, scenarios, magic items and the manual correction of a
 * pack level (frozen with the exam commit).
 *
 * Contract of the UI these tests drive (everything is French; the UI computes no plan, no forecast and
 * no ranking: it shows what the API answers; an unknown value is the badge "Inconnu", never 0; epochs
 * are shown with `Intl.DateTimeFormat` in the zone `tz` of `GET .../schedule`, as in E15; the word
 * "optimal" only comes from the proof label; no image at all). The current village is
 * `localStorage["clashpilot.village"]`; the tests set it, render `App` at the route and drive the
 * page. Server errors are `{detail: {code, message}}`: the French `message` is shown in a
 * `role="alert"`. Numbers are read with their thousands separators removed.
 *
 * Navigation. The main navigation (`nav`, name "Navigation principale") gains the links "Stratégie"
 * (`/strategie`), "Ressources" (`/ressources`) and "Objets magiques" (`/objets`), next to "Scénarios"
 * (`/scenarios`, no longer a placeholder). Each page has a heading (h1-h6) with the name of its link.
 *
 * Stratégie (`/strategie`). `GET /api/meta/strategies`: one `radio` per strategy, named with its
 * `label`, in a radio group. Nothing is chosen at first. The text field "Composition" takes unit ids
 * separated by commas or spaces. When the strategy `custom_goal` is chosen, and only then, the number
 * fields "Identifiant du but" and "Niveau du but" appear. The button "Calculer mon plan" sends
 * `POST .../plans` with a body limited to `strategy` (the key, left out when nothing is chosen),
 * `composition` (the ids as numbers, left out or empty when blank) and `goal {data_id, level}`
 * (numbers, `custom_goal` only): the server defaults rule engine, profile and horizon. A computed plan
 * is announced in a `role="status"` holding "Plan calculé". A failure shows the API `message` in a
 * `role="alert"`, with the link `/importer#saisie` when the problem is a `missing_input`.
 *
 * Ressources (`/ressources`, `GET .../forecast`). One `section` per resource of `series`, headed by its
 * French name (Or, Élixir...): an `svg` chart with one element `[data-series="low|expected|high"]` per
 * income, and the equivalent `table`: columns "Date", "Bas", "Attendu", "Haut", one row per point, the
 * three values of the point in the row. Sections headed "Saturations" (one row per window: resource,
 * start and end, spilled amount), "Ressource bloquante" (resource, delay in hours and minutes "2 h 30
 * min", task ids; "Aucune ressource bloquante" when `blocking` is null) and "Murs recommandés" (one row
 * per wall: name, "Niveau N", cost with the resource names, time `at`). Each of these sections and
 * every resource section holding known values carries the badge "Estimé". Where a value is unknown
 * (`status: "unknown"`) its cell shows the badge "Inconnu" and no digit, there is no "Estimé" in that
 * resource section, and the page holds a link to `/importer#saisie` (the manual input of `farm_rate`).
 *
 * Scénarios (`/scenarios`). `GET .../scenarios` lists one `li`, `tr` or `article` per scenario with its
 * name, a "Supprimer" button (`DELETE .../scenarios/{id}`, the entry then goes) and, for an `ok`
 * scenario, a checkbox named "Comparer {name}". A scenario `unsupported` shows "Non pris en charge", an
 * `infeasible` one "Infaisable", then each reason `message`; neither has a checkbox nor any result
 * (no table, no "Preuve", no "Tâches terminées"). The button "Comparer la sélection" sends
 * `POST .../scenarios/compare {scenario_ids}` and shows one `table`: columns (the API figures) "Scénario",
 * "Fin du but" (local time), "Tâches terminées", "Coût restant" (amount per resource, with its name),
 * "Valeur gagnée", "Héros indisponibles", "Connexions", "Constructeurs inactifs", "Preuve" (the proof
 * label); a first row "Plan réel" (the answer's `plan`), then one row per scenario in the answer's
 * order, each headed by its name. A null figure is the badge "Inconnu". The creation form: the text field
 * "Nom du scénario", the native select "Modification" with the seven overlays "Lancer une tâche maintenant"
 * (start_now: text "Tâche"), "Réserver des constructeurs" (reserve_builder: number "Nombre de constructeurs",
 * text or datetime-local "Jusqu'au (heure locale)", converted by `POST .../schedule/to-epoch` into `until`),
 * "Absence" (absence: "Début de l'absence", "Fin de l'absence", sent as `AAAA-MM-JJTHH:MM:SS`), "Politique de
 * connexion" (policy: select "Mode" with the labels of the settings page, "Durée de tolérance (minutes)",
 * "Nombre maximal de connexions par jour", the blank ones left out), "Stratégie" (strategy: select "Stratégie du
 * scénario" of the strategy labels, sending the key), "Jouer moins" (reduce_play: number "Nombre maximal de
 * connexions par jour") and "Utiliser un objet" (use_item: text "Objet", text "Tâche (facultative)" left out
 * when blank, "Heure d'utilisation (heure locale)" converted into `at`), and the button "Créer le scénario"
 * sending `POST .../scenarios {name, overlays: [one overlay]}` (no engine, no profile). The new scenario
 * then shows in the list; a failure shows the API `message` in a `role="alert"`.
 *
 * Objets magiques (`/objets`). The text field "Objet" (a rule key of the pack) and the button "Comparer"
 * send `POST .../items/compare {item}`; the button "Comparer les gemmes" sends `POST .../gems/compare {}`.
 * The answer: `status` "unsupported" shows "Non pris en charge" and the reason messages, no option.
 * `ok` shows the scope text, the proof label, and a `table` of the options in the API's order (it is
 * already ranked), columns "Option" ("Garder", "Utiliser", "Finir avec des gemmes"), "Tâche" (the
 * task id), "Variation du score" (the `score_delta`, `0` for Garder; "Inconnu" when null), "Conséquences"
 * (the French messages; for `gem_finish` the gems) . For gems a second section headed "Assistants" shows
 * `helpers` the same way (a row per upgrade: "niveau N" of `to_level`, the cost, the score cell), or "Non
 * pris en charge" and the reasons when its status says so. A failure shows the API `message` in an alert.
 *
 * Fiche d'un niveau (`/donnees/{pack_id}/{data_id}/{level}`, `GET /api/gamedata/packs/{pack_id}/items/
 * {data_id}`): the item name as a heading, "Niveau N", the source label, a form with the number fields
 * "Durée (secondes)" and "Coût {resource name}" (one per resource of `cost`), the text field "Note" and
 * the button "Enregistrer la correction". It sends `PATCH .../items/{data_id}/levels/{level}` with
 * `duration_s`, `cost` (every resource) and `note` only, then shows the answer: the level's `article`
 * says "Saisie manuelle" (only for a level that has an `original`), the note, and the imported
 * values (`original`). A 422 shows the API `message` in an alert and no "Saisie manuelle".
 */
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "../App";

// ---------------------------------------------------------------------------------- fetch stub

type Call = { method: string; path: string; search: string; body: string };
type Reply = { status?: number; body?: unknown };
type Handler = (call: Call) => Reply | undefined | Promise<Reply | undefined>;

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
  if (status === 204) {
    return new Response(null, { status });
  }
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

function sentBody(call: Call | undefined): Record<string, unknown> {
  if (call === undefined) {
    throw new Error("the call was not made");
  }
  return call.body === "" ? {} : (JSON.parse(call.body) as Record<string, unknown>);
}

/** Routes handlers one after the other: the first one that answers wins. */
function chain(...handlers: Handler[]): Handler {
  return async (call) => {
    for (const handler of handlers) {
      const reply = await handler(call);
      if (reply !== undefined) {
        return reply;
      }
    }
    return undefined;
  };
}

// -------------------------------------------------------------------------------------- fixtures

const TAG = "P2Y8QQ0";
const VILLAGE = `/api/villages/${TAG}`;
const ZONE = "Asia/Kolkata"; // UTC+5:30, no daylight saving time
const NOW = Date.UTC(2026, 10, 12, 12, 0, 0) / 1000; // 17:30 on 12/11/2026 in ZONE
const T_0415 = Date.UTC(2026, 10, 12, 22, 45, 0) / 1000; // 04:15 on 13/11/2026 in ZONE
const T_0415_NEXT = Date.UTC(2026, 10, 13, 22, 45, 0) / 1000; // 04:15 on 14/11/2026 in ZONE
const T_1730 = Date.UTC(2026, 10, 12, 12, 0, 0) / 1000; // 17:30 on 12/11/2026 in ZONE
const T_1900 = Date.UTC(2026, 10, 12, 13, 30, 0) / 1000; // 19:00 on 12/11/2026 in ZONE
const EPOCH = NOW - 777; // what the stub of /to-epoch answers
const TIME_0415 = /\b0?4\s*[:h]\s*15\b/;
const TIME_1730 = /\b17\s*[:h]\s*30\b/;
const TIME_1900 = /\b19\s*[:h]\s*00\b/;
const HEURISTIC_TEXT = "Solution heuristique, non prouvée optimale";

const PROFILE = {
  tz: ZONE,
  weekly: [],
  exceptions: [],
  policy: { mode: "strict", tolerance_minutes: 0, max_logins_per_day: null },
};

const HEURISTIC = { kind: "heuristic", objective: 10, bound: null, gap_abs: null, scope: "" };
const PACK = { pack_id: "pack-alpha", game_version: "7.1-test", fictional: false };
const explained = (code: string, message: string) => ({ code, message, value: null });
const RELIABILITY = { level: "medium", factors: [explained("estimated", "Données estimées.")] };

/** The routes every page may read: the schedule (zone) and the strategies. */
function common(): Handler {
  return (call) => {
    if (call.method === "GET" && call.path === `${VILLAGE}/schedule`) {
      return { body: PROFILE };
    }
    if (call.method === "GET" && call.path === "/api/meta/strategies") {
      return { body: { strategies: STRATEGIES } };
    }
    if (call.method === "POST" && call.path === `${VILLAGE}/schedule/to-epoch`) {
      return { body: { epoch: EPOCH } };
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

/** The same text with every space (thousands separators included) removed. */
function compact(node: Node): string {
  return textOf(node).replace(/[\s  ]/g, "");
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

/** The section (`section` element or role region) headed by `name`. */
async function sectionOf(name: string): Promise<HTMLElement> {
  const heading = await screen.findByRole("heading", { name });
  const section = heading.closest<HTMLElement>("section, [role='region']");
  if (section === null) {
    throw new Error(`the heading "${name}" is not inside a section`);
  }
  return section;
}

/** The cell of `row` under the column named `header` in `table`. */
function cellUnder(table: HTMLElement, row: HTMLElement, header: string): HTMLElement {
  const headerRow = within(table).getAllByRole("columnheader")[0]?.parentElement;
  if (headerRow === null || headerRow === undefined) {
    throw new Error("the table has no header row");
  }
  const index = [...headerRow.children].findIndex((cell) => textOf(cell) === header);
  const cell = row.children[index];
  if (index < 0 || cell === undefined) {
    throw new Error(`no column "${header}"`);
  }
  return cell as HTMLElement;
}

function bodyRows(table: HTMLElement): HTMLElement[] {
  return within(table)
    .getAllByRole("row")
    .filter((row) => within(row).queryAllByRole("columnheader").length === 0);
}

/** The table whose column headers include `header`, once it is on the page. */
async function tableWith(header: string): Promise<HTMLElement> {
  return waitFor(() => {
    const found = screen.queryAllByRole("table").find((table) =>
      within(table)
        .queryAllByRole("columnheader")
        .some((cell) => textOf(cell) === header),
    );
    if (found === undefined) {
      throw new Error(`no table with the column "${header}"`);
    }
    return found;
  });
}

function withoutProof(text: string): string {
  return text.replaceAll(HEURISTIC_TEXT, "");
}

async function expectAlert(text: string | RegExp): Promise<HTMLElement> {
  const alerts = await screen.findAllByRole("alert");
  const found = alerts.find((alert) =>
    typeof text === "string" ? textOf(alert).includes(text) : text.test(textOf(alert)),
  );
  if (found === undefined) {
    throw new Error(`no alert shows ${String(text)}`);
  }
  return found;
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

// -------------------------------------------------------------------------------- navigation

describe("Navigation", () => {
  it("adds Stratégie, Ressources and Objets magiques to the main navigation", async () => {
    installFetch(common());
    open("/");
    render(<App />);

    const nav = await screen.findByRole("navigation", { name: "Navigation principale" });
    const hrefOf = (name: string) =>
      within(nav).getByRole("link", { name }).getAttribute("href") ?? "";
    expect(hrefOf("Stratégie")).toBe("/strategie");
    expect(hrefOf("Ressources")).toBe("/ressources");
    expect(hrefOf("Objets magiques")).toBe("/objets");
    expect(hrefOf("Scénarios")).toBe("/scenarios");
  });

  it("opens each new page, with its heading and no image", async () => {
    installFetch(common());
    const user = userEvent.setup();
    open("/");
    render(<App />);

    const nav = await screen.findByRole("navigation", { name: "Navigation principale" });
    const pages = [
      ["Stratégie", "/strategie"],
      ["Ressources", "/ressources"],
      ["Objets magiques", "/objets"],
      ["Scénarios", "/scenarios"],
    ] as const;
    for (const [name, path] of pages) {
      await user.click(within(nav).getByRole("link", { name }));
      expect(await screen.findByRole("heading", { name })).toBeTruthy();
      expect(window.location.pathname).toBe(path);
      expect(document.querySelectorAll("img")).toHaveLength(0);
    }
  });

  it("no longer shows the placeholder text on the scenarios page", async () => {
    installFetch(common());
    open("/scenarios");
    render(<App />);

    expect(await screen.findByRole("heading", { name: "Scénarios" })).toBeTruthy();
    expect(bodyText()).not.toContain("Cette page arrive dans une prochaine version.");
  });
});

// ---------------------------------------------------------------------------------- Stratégie

const STRATEGIES = [
  ["balanced", "Équilibrée"],
  ["offense", "Attaque"],
  ["defense", "Défense"],
  ["th_rush", "Course à l'Hôtel de Ville"],
  ["heroes", "Héros d'abord"],
  ["war_cwl", "Guerre et ligue de clans"],
  ["low_play", "Peu de connexions"],
  ["resources", "Économie de ressources"],
  ["finish_th", "Finir l'Hôtel de Ville"],
  ["custom_goal", "Objectif personnalisé"],
].map(([key, label]) => ({ key, label, weights: { value: 1, idle: 0.1, night: 0.2 } }));

function planOut(overrides: Record<string, unknown> = {}) {
  return {
    plan_id: 8,
    now: NOW,
    timezone: ZONE,
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
    ...overrides,
  };
}

function strategyWorld(failure?: { status: number; body: unknown }): Handler {
  return chain(common(), (call) => {
    if (call.method === "POST" && call.path === `${VILLAGE}/plans`) {
      if (failure !== undefined) {
        return failure;
      }
      return { status: 201, body: planOut() };
    }
    return undefined;
  });
}

function planBody(): Record<string, unknown> {
  return sentBody(callsTo("POST", `${VILLAGE}/plans`)[0]);
}

describe("Stratégie", () => {
  it("lists the ten strategies of the API as radio buttons, none chosen at first", async () => {
    installFetch(strategyWorld());
    open("/strategie");
    render(<App />);

    for (const { label } of STRATEGIES) {
      const radio = (await screen.findByRole("radio", { name: label })) as HTMLInputElement;
      expect(radio.checked).toBe(false);
    }
    expect(screen.getAllByRole("radio")).toHaveLength(10);
    expect(callsTo("GET", "/api/meta/strategies").length).toBeGreaterThan(0);
    expect(callsTo("POST", `${VILLAGE}/plans`)).toHaveLength(0);
  });

  it("computes the plan with the chosen strategy and the typed composition", async () => {
    installFetch(strategyWorld());
    const user = userEvent.setup();
    open("/strategie");
    render(<App />);

    await user.click(await screen.findByRole("radio", { name: "Héros d'abord" }));
    await user.type(screen.getByLabelText("Composition"), "1000001, 1000002 1000003");
    expect(screen.queryByLabelText("Identifiant du but")).toBeNull();
    expect(screen.queryByLabelText("Niveau du but")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Calculer mon plan" }));

    await waitFor(() => expect(callsTo("POST", `${VILLAGE}/plans`)).toHaveLength(1));
    const body = planBody();
    expect(body.strategy).toBe("heroes");
    expect(body.composition).toEqual([1_000_001, 1_000_002, 1_000_003]);
    expect(body.goal ?? null).toBeNull();
    for (const key of Object.keys(body)) {
      expect(["strategy", "composition", "goal"]).toContain(key);
    }
    await waitFor(() =>
      expect(
        screen
          .queryAllByRole("status")
          .map((node) => textOf(node))
          .join(" "),
      ).toContain("Plan calculé"),
    );
  });

  it("asks for the goal of custom_goal only, and sends it", async () => {
    installFetch(strategyWorld());
    const user = userEvent.setup();
    open("/strategie");
    render(<App />);

    await user.click(await screen.findByRole("radio", { name: "Objectif personnalisé" }));
    fireEvent.change(await screen.findByLabelText("Identifiant du but"), {
      target: { value: "1000009" },
    });
    fireEvent.change(screen.getByLabelText("Niveau du but"), { target: { value: "7" } });
    await user.click(screen.getByRole("button", { name: "Calculer mon plan" }));

    await waitFor(() => expect(callsTo("POST", `${VILLAGE}/plans`)).toHaveLength(1));
    const body = planBody();
    expect(body.strategy).toBe("custom_goal");
    expect(body.goal).toEqual({ data_id: 1_000_009, level: 7 });
    expect(body.composition ?? []).toEqual([]);

    // Choosing another strategy takes the goal fields away and the goal out of the request.
    await user.click(screen.getByRole("radio", { name: "Attaque" }));
    expect(screen.queryByLabelText("Niveau du but")).toBeNull();
    await user.click(screen.getByRole("button", { name: "Calculer mon plan" }));
    await waitFor(() => expect(callsTo("POST", `${VILLAGE}/plans`)).toHaveLength(2));
    const second = sentBody(callsTo("POST", `${VILLAGE}/plans`)[1]);
    expect(second.strategy).toBe("offense");
    expect(second.goal ?? null).toBeNull();
  });

  it("sends no strategy when none is chosen", async () => {
    installFetch(strategyWorld());
    const user = userEvent.setup();
    open("/strategie");
    render(<App />);

    await screen.findByRole("radio", { name: "Équilibrée" });
    await user.click(screen.getByRole("button", { name: "Calculer mon plan" }));

    await waitFor(() => expect(callsTo("POST", `${VILLAGE}/plans`)).toHaveLength(1));
    const body = planBody();
    expect(body.strategy ?? null).toBeNull();
    expect(body.composition ?? []).toEqual([]);
    expect(body.goal ?? null).toBeNull();
    expect(body).not.toHaveProperty("engine");
    expect(body).not.toHaveProperty("profile");
    expect(body).not.toHaveProperty("horizon_days");
  });

  it("shows the API message of a failure, with the link to the manual input for missing data", async () => {
    installFetch(
      strategyWorld({
        status: 409,
        body: {
          detail: {
            code: "missing_input",
            message: "Des données manquent pour calculer le plan : resources.",
            missing: ["resources"],
          },
        },
      }),
    );
    const user = userEvent.setup();
    open("/strategie");
    render(<App />);

    await user.click(await screen.findByRole("radio", { name: "Défense" }));
    await user.click(screen.getByRole("button", { name: "Calculer mon plan" }));

    const alert = await expectAlert("Des données manquent pour calculer le plan");
    expect(alert.querySelector('a[href="/importer#saisie"]')).not.toBeNull();
    expect(
      screen
        .queryAllByRole("status")
        .map((node) => textOf(node))
        .join(" "),
    ).not.toContain("Plan calculé");
  });
});

// --------------------------------------------------------------------------------- Ressources

const estimate = (value: number) => ({ value, status: "estimated", source: "model", as_of: NOW });
const UNKNOWN = { value: null, status: "unknown", source: "model", as_of: null };
const DAY = 86_400;

function point(t: number, low: number, expected: number, high: number) {
  return { t, low: estimate(low), expected: estimate(expected), high: estimate(high) };
}

function forecast(overrides: Record<string, unknown> = {}) {
  return {
    plan_id: 7,
    now: NOW,
    horizon_end: NOW + 3 * DAY,
    step_s: DAY,
    series: {
      gold: [
        point(NOW, 120_400, 120_500, 120_600),
        point(NOW + DAY, 150_700, 160_800, 170_900),
        point(NOW + 2 * DAY, 190_100, 210_200, 230_300),
      ],
      elixir: [
        point(NOW, 80_400, 80_500, 80_600),
        point(NOW + DAY, 60_700, 70_800, 90_900),
        point(NOW + 2 * DAY, 40_100, 50_200, 70_300),
      ],
    },
    funding: [],
    saturation: [{ resource: "gold", start: T_0415, end: T_0415 + 7_200, spilled: 4_321 }],
    blocking: { resource: "elixir", delayed_s: 9_000, tasks: ["t-1", "t-2"] },
    walls: [
      {
        data_id: 1_000_099,
        instance: 3,
        level: 6,
        name: "Mur d'essai",
        cost: { gold: 54_321 },
        at: T_0415_NEXT,
      },
    ],
    effort: { scope: "plan", cost: { gold: 1 }, farm_hours: {} },
    ...overrides,
  };
}

function unknownForecast() {
  const blank = (t: number) => ({ t, low: UNKNOWN, expected: UNKNOWN, high: UNKNOWN });
  return forecast({
    series: {
      gold: [blank(NOW), blank(NOW + DAY), blank(NOW + 2 * DAY)],
      elixir: [blank(NOW), blank(NOW + DAY), blank(NOW + 2 * DAY)],
    },
    saturation: [],
    blocking: null,
    walls: [],
  });
}

function resourcesWorld(answer: Reply): Handler {
  return chain(common(), (call) =>
    call.method === "GET" && call.path === `${VILLAGE}/forecast` ? answer : undefined,
  );
}

describe("Ressources", () => {
  it("draws one chart and one equivalent table per resource, each marked Estimé", async () => {
    installFetch(resourcesWorld({ body: forecast() }));
    open("/ressources");
    render(<App />);

    const expected: Record<string, string[][]> = {
      Or: [
        ["120400", "120500", "120600"],
        ["150700", "160800", "170900"],
        ["190100", "210200", "230300"],
      ],
      Élixir: [
        ["80400", "80500", "80600"],
        ["60700", "70800", "90900"],
        ["40100", "50200", "70300"],
      ],
    };
    for (const [name, points] of Object.entries(expected)) {
      const section = await sectionOf(name);
      expect(textOf(section)).toContain("Estimé");
      const svg = section.querySelector("svg");
      expect(svg, `${name} chart`).not.toBeNull();
      for (const series of ["low", "expected", "high"]) {
        expect(
          section.querySelectorAll(`svg [data-series="${series}"]`).length,
          `${name} ${series}`,
        ).toBeGreaterThan(0);
      }
      const table = within(section).getByRole("table");
      const headers = within(table)
        .getAllByRole("columnheader")
        .map((header) => textOf(header));
      expect(headers).toEqual(expect.arrayContaining(["Date", "Bas", "Attendu", "Haut"]));
      const rows = bodyRows(table);
      expect(rows).toHaveLength(3);
      rows.forEach((row, index) => {
        const text = compact(row);
        for (const value of points[index] ?? []) {
          expect(text, `${name} row ${index}`).toContain(value);
        }
      });
    }
    expect(callsTo("GET", `${VILLAGE}/forecast`).length).toBeGreaterThan(0);
  });

  it("lists the saturation windows, marked Estimé", async () => {
    installFetch(resourcesWorld({ body: forecast() }));
    open("/ressources");
    render(<App />);

    const section = await sectionOf("Saturations");
    expect(textOf(section)).toContain("Estimé");
    const row = await rowWith("Or", section);
    expect(compact(row)).toContain("4321");
    expect(textOf(row)).toMatch(TIME_0415);
  });

  it("names the blocking resource, its delay and its tasks, marked Estimé", async () => {
    installFetch(resourcesWorld({ body: forecast() }));
    open("/ressources");
    render(<App />);

    const section = await sectionOf("Ressource bloquante");
    expect(textOf(section)).toContain("Estimé");
    const text = textOf(section);
    expect(text).toContain("Élixir");
    expect(text).toMatch(/2\s*h\s*30/);
    expect(text).toContain("t-1");
    expect(text).toContain("t-2");
  });

  it("says that no resource blocks when the API names none", async () => {
    installFetch(resourcesWorld({ body: forecast({ blocking: null }) }));
    open("/ressources");
    render(<App />);

    const section = await sectionOf("Ressource bloquante");
    expect(textOf(section)).toContain("Aucune ressource bloquante");
  });

  it("lists the recommended walls with their level, cost and time, marked Estimé", async () => {
    installFetch(resourcesWorld({ body: forecast() }));
    open("/ressources");
    render(<App />);

    const section = await sectionOf("Murs recommandés");
    expect(textOf(section)).toContain("Estimé");
    const row = await rowWith("Mur d'essai", section);
    expect(textOf(row)).toMatch(/Niveau\s*6/);
    expect(compact(row)).toContain("54321");
    expect(textOf(row)).toContain("Or");
    expect(textOf(row)).toMatch(TIME_0415);
  });

  it("shows Inconnu, never 0, with a link to the manual input when there is no farm_rate", async () => {
    installFetch(resourcesWorld({ body: unknownForecast() }));
    open("/ressources");
    render(<App />);

    for (const name of ["Or", "Élixir"]) {
      const section = await sectionOf(name);
      expect(textOf(section)).toContain("Inconnu");
      expect(textOf(section)).not.toContain("Estimé");
      const rows = bodyRows(within(section).getByRole("table"));
      expect(rows).toHaveLength(3);
      for (const row of rows) {
        const values = [...row.children].slice(1);
        expect(values).toHaveLength(3);
        for (const cell of values) {
          expect(textOf(cell)).toBe("Inconnu");
        }
      }
    }
    const link = document.querySelector('a[href="/importer#saisie"]');
    expect(link).not.toBeNull();
  });

  it("shows the API message when there is no plan to forecast", async () => {
    installFetch(
      resourcesWorld({
        status: 404,
        body: { detail: { code: "no_plan", message: "Aucun plan n'a encore été calculé." } },
      }),
    );
    open("/ressources");
    render(<App />);

    await expectAlert("Aucun plan n'a encore été calculé.");
    expect(document.querySelectorAll("svg [data-series]")).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------------- Scénarios

const OK_REASONS: unknown[] = [];

function scenario(id: number, name: string, overrides: Record<string, unknown> = {}) {
  return {
    scenario_id: id,
    plan_id: 7,
    name,
    overlays: [{ type: "start_now", task_id: "t-1" }],
    status: "ok",
    reasons: OK_REASONS,
    now: NOW,
    horizon_end: NOW + 90 * DAY,
    timezone: ZONE,
    engine: "auto",
    profile: "fast",
    assignments: [],
    proof: HEURISTIC,
    warnings: [],
    ...overrides,
  };
}

const S_NOW = scenario(11, "Départ immédiat");
const S_AWAY = scenario(12, "Semaine absente", {
  overlays: [
    { type: "absence", start_local: "2026-11-14T08:00:00", end_local: "2026-11-16T20:00:00" },
  ],
});
const S_ITEM = scenario(13, "Objet sans règle", {
  overlays: [{ type: "use_item", item: "mystery", task_id: null, at: NOW }],
  status: "unsupported",
  reasons: [{ code: "unsupported_item", message: "Cet objet n'a pas de règle dans le pack." }],
  assignments: [],
  proof: null,
});
const S_FUNDS = scenario(14, "Sans fonds", {
  status: "infeasible",
  reasons: [{ code: "insufficient_funds", message: "Les fonds manquent pour cette tâche." }],
  assignments: [],
  proof: null,
});

function figures(overrides: Record<string, unknown>) {
  return {
    goal_finish: null,
    finished_count: null,
    remaining_cost: null,
    value_gained: null,
    hero_unavailable_s: null,
    logins: null,
    idle_builder_s: null,
    proof: null,
    ...overrides,
  };
}

const COMPARISON = {
  plan: {
    plan_id: 7,
    ...figures({
      goal_finish: T_0415,
      finished_count: 41,
      remaining_cost: { gold: 123_456, elixir: 7_890 },
      value_gained: 321,
      hero_unavailable_s: 7_200,
      logins: 6,
      idle_builder_s: 3_600,
      proof: HEURISTIC,
    }),
  },
  scenarios: [
    {
      scenario_id: 11,
      name: "Départ immédiat",
      status: "ok",
      ...figures({
        goal_finish: T_1730,
        finished_count: 43,
        remaining_cost: { gold: 98_765 },
        value_gained: 355,
        hero_unavailable_s: 3_600,
        logins: 5,
        idle_builder_s: 1_800,
        proof: HEURISTIC,
      }),
    },
    {
      scenario_id: 12,
      name: "Semaine absente",
      status: "ok",
      ...figures({
        goal_finish: T_1900,
        finished_count: 40,
        remaining_cost: { elixir: 45_678 },
        value_gained: null,
        hero_unavailable_s: 0,
        logins: 4,
        idle_builder_s: null,
        proof: HEURISTIC,
      }),
    },
  ],
};

type ScenarioWorldOptions = {
  initial?: unknown[];
  creation?: Reply;
};

/** The scenarios of the village, stateful: a created scenario joins the list, a deleted one leaves. */
function scenarioWorld({
  initial = [S_NOW, S_AWAY, S_ITEM, S_FUNDS],
  creation,
}: ScenarioWorldOptions) {
  const stored = [...initial] as Array<{ scenario_id: number }>;
  let next = 20;
  return chain(common(), (call) => {
    if (call.method === "GET" && call.path === `${VILLAGE}/scenarios`) {
      return { body: stored };
    }
    if (call.method === "POST" && call.path === `${VILLAGE}/scenarios`) {
      if (creation !== undefined) {
        return creation;
      }
      const body = sentBody(call);
      const created = scenario(next, String(body.name), { overlays: body.overlays });
      next += 1;
      stored.push(created);
      return { status: 201, body: created };
    }
    if (call.method === "POST" && call.path === `${VILLAGE}/scenarios/compare`) {
      return { body: COMPARISON };
    }
    const found = /\/scenarios\/(\d+)$/.exec(call.path);
    if (call.method === "DELETE" && found !== null) {
      const index = stored.findIndex((s) => s.scenario_id === Number(found[1]));
      if (index < 0) {
        return {
          status: 404,
          body: { detail: { code: "scenario_not_found", message: "Scénario introuvable." } },
        };
      }
      stored.splice(index, 1);
      return { status: 204 };
    }
    return undefined;
  });
}

describe("Scénarios", () => {
  it("lists the scenarios, with a checkbox to compare the ok ones only", async () => {
    installFetch(scenarioWorld({}));
    open("/scenarios");
    render(<App />);

    for (const name of ["Départ immédiat", "Semaine absente", "Objet sans règle", "Sans fonds"]) {
      await rowWith(name);
    }
    expect(screen.getByRole("checkbox", { name: "Comparer Départ immédiat" })).toBeTruthy();
    expect(screen.getByRole("checkbox", { name: "Comparer Semaine absente" })).toBeTruthy();
    expect(screen.queryByRole("checkbox", { name: "Comparer Objet sans règle" })).toBeNull();
    expect(screen.queryByRole("checkbox", { name: "Comparer Sans fonds" })).toBeNull();
    expect(screen.getAllByRole("checkbox")).toHaveLength(2);
  });

  it("shows the reason of an unsupported or infeasible scenario and no result figure", async () => {
    installFetch(scenarioWorld({}));
    open("/scenarios");
    render(<App />);

    const unsupported = await rowWith("Objet sans règle");
    expect(textOf(unsupported)).toContain("Non pris en charge");
    expect(textOf(unsupported)).toContain("Cet objet n'a pas de règle dans le pack.");
    const infeasible = await rowWith("Sans fonds");
    expect(textOf(infeasible)).toContain("Infaisable");
    expect(textOf(infeasible)).toContain("Les fonds manquent pour cette tâche.");
    for (const entry of [unsupported, infeasible]) {
      expect(within(entry).queryAllByRole("table")).toHaveLength(0);
      for (const figure of ["Preuve", "Tâches terminées", "Fin du but", "Estimé"]) {
        expect(textOf(entry)).not.toContain(figure);
      }
      expect(within(entry).getByRole("button", { name: "Supprimer" })).toBeTruthy();
    }
    expect(callsTo("POST", `${VILLAGE}/scenarios/compare`)).toHaveLength(0);
  });

  it("compares the chosen scenarios with the real plan, column by column", async () => {
    installFetch(scenarioWorld({}));
    const user = userEvent.setup();
    open("/scenarios");
    render(<App />);

    await user.click(await screen.findByRole("checkbox", { name: "Comparer Départ immédiat" }));
    await user.click(screen.getByRole("checkbox", { name: "Comparer Semaine absente" }));
    await user.click(screen.getByRole("button", { name: "Comparer la sélection" }));

    await waitFor(() => expect(callsTo("POST", `${VILLAGE}/scenarios/compare`)).toHaveLength(1));
    const asked = sentBody(callsTo("POST", `${VILLAGE}/scenarios/compare`)[0]);
    expect(Object.keys(asked)).toEqual(["scenario_ids"]);
    expect((asked.scenario_ids as number[]).slice().sort()).toEqual([11, 12]);

    const table = await tableWith("Preuve");
    const headers = within(table)
      .getAllByRole("columnheader")
      .map((header) => textOf(header));
    expect(headers).toEqual([
      "Scénario",
      "Fin du but",
      "Tâches terminées",
      "Coût restant",
      "Valeur gagnée",
      "Héros indisponibles",
      "Connexions",
      "Constructeurs inactifs",
      "Preuve",
    ]);
    const rows = bodyRows(table);
    expect(rows.map((row) => textOf(row.children[0] as Element))).toEqual([
      "Plan réel",
      "Départ immédiat",
      "Semaine absente",
    ]);
    const [real, now, away] = rows as [HTMLElement, HTMLElement, HTMLElement];

    expect(textOf(cellUnder(table, real, "Fin du but"))).toMatch(TIME_0415);
    expect(textOf(cellUnder(table, now, "Fin du but"))).toMatch(TIME_1730);
    expect(textOf(cellUnder(table, away, "Fin du but"))).toMatch(TIME_1900);
    expect(compact(cellUnder(table, real, "Tâches terminées"))).toContain("41");
    expect(compact(cellUnder(table, now, "Tâches terminées"))).toContain("43");
    expect(compact(cellUnder(table, away, "Tâches terminées"))).toContain("40");

    const realCost = cellUnder(table, real, "Coût restant");
    expect(compact(realCost)).toContain("123456");
    expect(compact(realCost)).toContain("7890");
    expect(textOf(realCost)).toContain("Or");
    expect(textOf(realCost)).toContain("Élixir");
    expect(compact(cellUnder(table, now, "Coût restant"))).toContain("98765");
    expect(compact(cellUnder(table, away, "Coût restant"))).toContain("45678");
    expect(compact(cellUnder(table, now, "Valeur gagnée"))).toContain("355");
    expect(textOf(cellUnder(table, real, "Preuve"))).toContain(HEURISTIC_TEXT);
  });

  it("shows an unknown figure as Inconnu, never as 0, and says optimale only in the proof", async () => {
    installFetch(scenarioWorld({}));
    const user = userEvent.setup();
    open("/scenarios");
    render(<App />);

    await user.click(await screen.findByRole("checkbox", { name: "Comparer Semaine absente" }));
    await user.click(screen.getByRole("button", { name: "Comparer la sélection" }));

    const table = await tableWith("Preuve");
    const away = bodyRows(table).find((row) => textOf(row).includes("Semaine absente"));
    expect(away).toBeDefined();
    for (const header of ["Valeur gagnée", "Constructeurs inactifs"]) {
      expect(textOf(cellUnder(table, away as HTMLElement, header))).toBe("Inconnu");
    }
    expect(withoutProof(bodyText())).not.toMatch(/optimal/i);
  });

  it("deletes a scenario", async () => {
    installFetch(scenarioWorld({}));
    const user = userEvent.setup();
    open("/scenarios");
    render(<App />);

    const entry = await rowWith("Départ immédiat");
    await user.click(within(entry).getByRole("button", { name: "Supprimer" }));

    await waitFor(() => expect(callsTo("DELETE", `${VILLAGE}/scenarios/11`)).toHaveLength(1));
    await waitFor(() => expect(screen.queryByText("Départ immédiat")).toBeNull());
    expect(screen.getByText("Semaine absente")).toBeTruthy();
    expect(callsTo("DELETE", `${VILLAGE}/scenarios/12`)).toHaveLength(0);
  });
});

describe("Scénarios : création", () => {
  const OVERLAY_LABELS = [
    "Lancer une tâche maintenant",
    "Réserver des constructeurs",
    "Absence",
    "Politique de connexion",
    "Stratégie",
    "Jouer moins",
    "Utiliser un objet",
  ];

  async function startForm(user: User, overlay: string): Promise<void> {
    await user.type(await screen.findByLabelText("Nom du scénario"), "Mon essai");
    await user.selectOptions(screen.getByLabelText("Modification"), overlay);
  }

  it("offers the seven overlays of the API", async () => {
    installFetch(scenarioWorld({ initial: [] }));
    open("/scenarios");
    render(<App />);

    const select = (await screen.findByLabelText("Modification")) as HTMLSelectElement;
    const labels = [...select.options].map((option) => option.textContent?.trim());
    for (const label of OVERLAY_LABELS) {
      expect(labels).toContain(label);
    }
    expect(screen.getByRole("button", { name: "Créer le scénario" })).toBeTruthy();
  });

  type Case = {
    overlay: string;
    fill: (user: User) => Promise<void>;
    expected: Record<string, unknown>;
    converted?: string;
  };
  const setField = (label: string, value: string): void => {
    fireEvent.change(screen.getByLabelText(label), { target: { value } });
  };

  const CASES: Case[] = [
    {
      overlay: "Lancer une tâche maintenant",
      fill: async () => setField("Tâche", "t-1"),
      expected: { type: "start_now", task_id: "t-1" },
    },
    {
      overlay: "Réserver des constructeurs",
      fill: async () => {
        setField("Nombre de constructeurs", "2");
        setField("Jusqu'au (heure locale)", "2026-11-14T08:00");
      },
      expected: { type: "reserve_builder", count: 2, until: EPOCH },
      converted: "2026-11-14T08:00",
    },
    {
      overlay: "Absence",
      fill: async () => {
        setField("Début de l'absence", "2026-11-14T08:00");
        setField("Fin de l'absence", "2026-11-16T20:00");
      },
      expected: {
        type: "absence",
        start_local: "2026-11-14T08:00:00",
        end_local: "2026-11-16T20:00:00",
      },
    },
    {
      overlay: "Politique de connexion",
      fill: async (user) => {
        await user.selectOptions(screen.getByLabelText("Mode"), "Tolérer une inactivité limitée");
        setField("Durée de tolérance (minutes)", "20");
      },
      expected: { type: "policy", mode: "tolerance", tolerance_minutes: 20 },
    },
    {
      overlay: "Stratégie",
      fill: async (user) => {
        await user.selectOptions(screen.getByLabelText("Stratégie du scénario"), "Héros d'abord");
      },
      expected: { type: "strategy", key: "heroes" },
    },
    {
      overlay: "Jouer moins",
      fill: async () => setField("Nombre maximal de connexions par jour", "2"),
      expected: { type: "reduce_play", max_logins_per_day: 2 },
    },
    {
      overlay: "Utiliser un objet",
      fill: async () => {
        setField("Objet", "book_of_building");
        setField("Tâche (facultative)", "t-1");
        setField("Heure d'utilisation (heure locale)", "2026-11-14T09:30");
      },
      expected: { type: "use_item", item: "book_of_building", task_id: "t-1", at: EPOCH },
      converted: "2026-11-14T09:30",
    },
  ];

  it.each(CASES)("creates a scenario with the overlay $overlay", async (item) => {
    installFetch(scenarioWorld({ initial: [] }));
    const user = userEvent.setup();
    open("/scenarios");
    render(<App />);

    await startForm(user, item.overlay);
    await item.fill(user);
    await user.click(screen.getByRole("button", { name: "Créer le scénario" }));

    await waitFor(() => expect(callsTo("POST", `${VILLAGE}/scenarios`)).toHaveLength(1));
    const body = sentBody(callsTo("POST", `${VILLAGE}/scenarios`)[0]);
    expect(body).toEqual({ name: "Mon essai", overlays: [item.expected] });
    if (item.converted !== undefined) {
      const conversion = callsTo("POST", `${VILLAGE}/schedule/to-epoch`);
      expect(conversion).toHaveLength(1);
      expect(sentBody(conversion[0]).local_datetime).toMatch(
        new RegExp(`^${item.converted}(:00)?$`),
      );
    }
    expect(await screen.findByText("Mon essai")).toBeTruthy();
  });

  it("leaves a blank optional task out of a use_item overlay", async () => {
    installFetch(scenarioWorld({ initial: [] }));
    const user = userEvent.setup();
    open("/scenarios");
    render(<App />);

    await startForm(user, "Utiliser un objet");
    setField("Objet", "builder_potion");
    setField("Heure d'utilisation (heure locale)", "2026-11-14T09:30");
    await user.click(screen.getByRole("button", { name: "Créer le scénario" }));

    await waitFor(() => expect(callsTo("POST", `${VILLAGE}/scenarios`)).toHaveLength(1));
    const [overlay] = sentBody(callsTo("POST", `${VILLAGE}/scenarios`)[0]).overlays as Array<
      Record<string, unknown>
    >;
    expect(overlay).toEqual({ type: "use_item", item: "builder_potion", at: EPOCH });
  });

  it("shows the API message when the scenario cannot be created", async () => {
    installFetch(
      scenarioWorld({
        initial: [],
        creation: {
          status: 404,
          body: { detail: { code: "no_plan", message: "Aucun plan n'a encore été calculé." } },
        },
      }),
    );
    const user = userEvent.setup();
    open("/scenarios");
    render(<App />);

    await startForm(user, "Jouer moins");
    setField("Nombre maximal de connexions par jour", "2");
    await user.click(screen.getByRole("button", { name: "Créer le scénario" }));

    await expectAlert("Aucun plan n'a encore été calculé.");
    expect(screen.queryByText("Mon essai")).toBeNull();
  });
});

// ------------------------------------------------------------------------------ Objets magiques

const consequence = (code: string, message: string, extra: Record<string, unknown> = {}) => ({
  code,
  message,
  task_id: null,
  end_before: null,
  end_after: null,
  resource: null,
  before: null,
  after: null,
  ...extra,
});

function option(overrides: Record<string, unknown>) {
  return {
    kind: "use",
    task_id: "t-1",
    at: NOW,
    score_delta: 0,
    finish_delta_s: 0,
    consequences: [],
    reliability: RELIABILITY,
    ...overrides,
  };
}

const ITEM_ANSWER = {
  status: "ok",
  reasons: [],
  options: [
    option({
      task_id: "t-2",
      score_delta: 2.5,
      consequences: [consequence("job_shortened", "La tâche t-2 finit 1 h plus tôt.")],
    }),
    option({ task_id: "t-3", score_delta: 0.75 }),
    option({ kind: "keep", task_id: null, at: null, score_delta: 0 }),
    option({
      task_id: "t-1",
      score_delta: -1.25,
      consequences: [consequence("resource_filled", "Le stock d'or se remplit plus vite.")],
    }),
  ],
  proof: HEURISTIC,
  reference_plan_id: 7,
  scope: "Comparaison sur 3 tâches candidates",
};

const GEMS_ANSWER = {
  status: "ok",
  reasons: [],
  options: [
    option({
      kind: "gem_finish",
      task_id: "t-4",
      score_delta: 1.5,
      gems: 1_234,
      consequences: [consequence("job_shortened", "La tâche t-4 finit tout de suite.")],
    }),
    option({ kind: "keep", task_id: null, at: null, score_delta: 0 }),
  ],
  proof: HEURISTIC,
  reference_plan_id: 7,
  scope: "Comparaison sur 1 amélioration en cours",
  helpers: {
    status: "ok",
    reasons: [],
    options: [
      {
        data_id: 1_000_500,
        from_level: 4,
        to_level: 5,
        cost: { gold: 65_432 },
        score_delta: null,
        consequences: [],
        reliability: RELIABILITY,
      },
    ],
  },
};

const UNSUPPORTED_ANSWER = {
  status: "unsupported",
  reasons: [{ code: "unsupported_item", message: "Cet objet n'a pas de règle dans le pack." }],
  options: [],
  proof: null,
  reference_plan_id: 7,
  scope: "",
};

function itemsWorld(item: Reply, gems: Reply = { body: GEMS_ANSWER }): Handler {
  return chain(common(), (call) => {
    if (call.method === "POST" && call.path === `${VILLAGE}/items/compare`) {
      return item;
    }
    if (call.method === "POST" && call.path === `${VILLAGE}/gems/compare`) {
      return gems;
    }
    return undefined;
  });
}

async function compareItem(user: User, key: string): Promise<void> {
  await user.type(await screen.findByLabelText("Objet"), key);
  await user.click(screen.getByRole("button", { name: "Comparer" }));
}

describe("Objets magiques", () => {
  it("compares an item and shows the options in the order of the API, Garder included", async () => {
    installFetch(itemsWorld({ body: ITEM_ANSWER }));
    const user = userEvent.setup();
    open("/objets");
    render(<App />);

    await compareItem(user, "book_of_building");

    await waitFor(() => expect(callsTo("POST", `${VILLAGE}/items/compare`)).toHaveLength(1));
    expect(sentBody(callsTo("POST", `${VILLAGE}/items/compare`)[0])).toEqual({
      item: "book_of_building",
    });
    const table = await tableWith("Variation du score");
    const rows = bodyRows(table);
    expect(rows).toHaveLength(4);
    const kinds = rows.map((row) => textOf(cellUnder(table, row, "Option")));
    expect(kinds).toEqual(["Utiliser", "Utiliser", "Garder", "Utiliser"]);
    const tasks = rows.map((row) => textOf(cellUnder(table, row, "Tâche")));
    expect(tasks[0]).toBe("t-2");
    expect(tasks[1]).toBe("t-3");
    expect(tasks[3]).toBe("t-1");
    const delta = (index: number) =>
      textOf(cellUnder(table, rows[index] as HTMLElement, "Variation du score"));
    expect(delta(0)).toMatch(/2[.,]5\b/);
    expect(delta(1)).toMatch(/0[.,]75\b/);
    expect(delta(2)).toMatch(/^[+\-−]?\s?0([.,]0+)?$/);
    expect(delta(3)).toMatch(/[-−–]\s?1[.,]25\b/);
    expect(textOf(cellUnder(table, rows[0] as HTMLElement, "Conséquences"))).toContain(
      "La tâche t-2 finit 1 h plus tôt.",
    );
    expect(textOf(cellUnder(table, rows[3] as HTMLElement, "Conséquences"))).toContain(
      "Le stock d'or se remplit plus vite.",
    );
    expect(bodyText()).toContain("Comparaison sur 3 tâches candidates");
    expect(bodyText()).toContain(HEURISTIC_TEXT);
    expect(withoutProof(bodyText())).not.toMatch(/optimal/i);
  });

  it("says Non pris en charge, with the reasons and no option, for an unsupported item", async () => {
    installFetch(itemsWorld({ body: UNSUPPORTED_ANSWER }));
    const user = userEvent.setup();
    open("/objets");
    render(<App />);

    await compareItem(user, "mystery");

    expect(await screen.findByText(/Non pris en charge/)).toBeTruthy();
    expect(bodyText()).toContain("Cet objet n'a pas de règle dans le pack.");
    expect(screen.queryAllByRole("table")).toHaveLength(0);
    expect(screen.queryByText("Garder")).toBeNull();
  });

  it("shows the API message when the comparison fails", async () => {
    installFetch(
      itemsWorld({
        status: 409,
        body: {
          detail: {
            code: "missing_input",
            message: "Des données manquent pour comparer : resources.",
            missing: ["resources"],
          },
        },
      }),
    );
    const user = userEvent.setup();
    open("/objets");
    render(<App />);

    await compareItem(user, "book_of_building");

    const alert = await expectAlert("Des données manquent pour comparer");
    expect(alert.querySelector('a[href="/importer#saisie"]')).not.toBeNull();
    expect(screen.queryAllByRole("table")).toHaveLength(0);
  });

  it("compares the gems and prices the helpers, their score being unknown", async () => {
    installFetch(itemsWorld({ body: ITEM_ANSWER }));
    const user = userEvent.setup();
    open("/objets");
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Comparer les gemmes" }));

    await waitFor(() => expect(callsTo("POST", `${VILLAGE}/gems/compare`)).toHaveLength(1));
    expect(sentBody(callsTo("POST", `${VILLAGE}/gems/compare`)[0])).toEqual({});

    const helpers = await sectionOf("Assistants");
    const helperTable = within(helpers).getByRole("table");
    const [helperRow] = bodyRows(helperTable);
    expect(textOf(helperRow as HTMLElement)).toMatch(/niveau\s*5/i);
    expect(compact(helperRow as HTMLElement)).toContain("65432");
    expect(textOf(cellUnder(helperTable, helperRow as HTMLElement, "Variation du score"))).toBe(
      "Inconnu",
    );

    const tables = screen.getAllByRole("table");
    const optionTable = tables.find((table) => !helpers.contains(table)) as HTMLElement;
    const rows = bodyRows(optionTable);
    expect(rows.map((row) => textOf(cellUnder(optionTable, row, "Option")))).toEqual([
      "Finir avec des gemmes",
      "Garder",
    ]);
    expect(compact(rows[0] as HTMLElement)).toContain("1234");
    expect(textOf(cellUnder(optionTable, rows[0] as HTMLElement, "Variation du score"))).toMatch(
      /1[.,]5\b/,
    );
    expect(withoutProof(bodyText())).not.toMatch(/optimal/i);
  });

  it("says Non pris en charge in the helpers section when they have no rule", async () => {
    installFetch(
      itemsWorld(
        { body: ITEM_ANSWER },
        {
          body: {
            ...GEMS_ANSWER,
            helpers: {
              status: "unsupported",
              reasons: [
                { code: "no_helper_rule", message: "Aucune règle de coût pour les assistants." },
              ],
              options: [],
            },
          },
        },
      ),
    );
    const user = userEvent.setup();
    open("/objets");
    render(<App />);

    await user.click(await screen.findByRole("button", { name: "Comparer les gemmes" }));

    const helpers = await sectionOf("Assistants");
    expect(textOf(helpers)).toContain("Non pris en charge");
    expect(textOf(helpers)).toContain("Aucune règle de coût pour les assistants.");
    expect(within(helpers).queryAllByRole("table")).toHaveLength(0);
  });
});

// ------------------------------------------------------------------- fiche d'un niveau du pack

const PACK_ID = "pack-alpha";
const DATA_ID = 1_000_001;
const ITEM_PATH = `/api/gamedata/packs/${PACK_ID}/items/${DATA_ID}`;
const LEVEL_PATH = `${ITEM_PATH}/levels/3`;

const SOURCE = {
  id: "s1",
  label: "Source fictive A",
  url: null,
  retrieved_at: "2026-01-01",
  license: null,
};

function levelRow(level: number, overrides: Record<string, unknown> = {}) {
  return {
    level,
    duration_s: 3_600,
    cost: { gold: 3_000, elixir: 500 },
    requires: [],
    source: SOURCE,
    original: null,
    note: null,
    ...overrides,
  };
}

function itemLevels(third: Record<string, unknown> = {}) {
  return {
    pack_id: PACK_ID,
    data_id: DATA_ID,
    name: "Canon d'essai",
    kind: "building",
    lane: "builder",
    category: "defense",
    tags: [],
    max_level: 4,
    levels: [levelRow(1), levelRow(2), levelRow(3, third), levelRow(4)],
  };
}

const CORRECTED = itemLevels({
  duration_s: 7_200,
  cost: { gold: 4_500, elixir: 500 },
  original: { duration_s: 3_600, cost: { gold: 3_000, elixir: 500 } },
  note: "Vu en jeu",
});

function levelWorld(patch: Reply = { body: CORRECTED }): Handler {
  return (call) => {
    if (call.method === "GET" && call.path === ITEM_PATH) {
      return { body: itemLevels() };
    }
    if (call.method === "PATCH" && call.path === LEVEL_PATH) {
      return patch;
    }
    return undefined;
  };
}

describe("Fiche d'un niveau du pack", () => {
  const ROUTE = `/donnees/${PACK_ID}/${DATA_ID}/3`;

  it("shows the level with its source and a correction form, without Saisie manuelle", async () => {
    installFetch(levelWorld());
    open(ROUTE);
    render(<App />);

    expect(await screen.findByRole("heading", { name: /Canon d'essai/ })).toBeTruthy();
    expect(bodyText()).toMatch(/Niveau\s*3/);
    expect(bodyText()).toContain("Source fictive A");
    expect((screen.getByLabelText("Durée (secondes)") as HTMLInputElement).value).toBe("3600");
    expect((screen.getByLabelText("Coût Or") as HTMLInputElement).value).toBe("3000");
    expect((screen.getByLabelText("Coût Élixir") as HTMLInputElement).value).toBe("500");
    expect(screen.getByLabelText("Note")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Enregistrer la correction" })).toBeTruthy();
    expect(bodyText()).not.toContain("Saisie manuelle");
    expect(callsTo("PATCH", LEVEL_PATH)).toHaveLength(0);
  });

  it("sends the correction by PATCH, then shows it with the mention Saisie manuelle", async () => {
    installFetch(levelWorld());
    const user = userEvent.setup();
    open(ROUTE);
    render(<App />);

    await screen.findByLabelText("Durée (secondes)");
    fireEvent.change(screen.getByLabelText("Durée (secondes)"), { target: { value: "7200" } });
    fireEvent.change(screen.getByLabelText("Coût Or"), { target: { value: "4500" } });
    await user.type(screen.getByLabelText("Note"), "Vu en jeu");
    await user.click(screen.getByRole("button", { name: "Enregistrer la correction" }));

    await waitFor(() => expect(callsTo("PATCH", LEVEL_PATH)).toHaveLength(1));
    const body = sentBody(callsTo("PATCH", LEVEL_PATH)[0]);
    expect(body.duration_s).toBe(7_200);
    expect(body.cost).toMatchObject({ gold: 4_500 });
    expect(body.note).toBe("Vu en jeu");
    for (const key of Object.keys(body)) {
      expect(["duration_s", "cost", "note"]).toContain(key);
    }

    const [mention] = await screen.findAllByText(/Saisie manuelle/);
    const article = (mention as HTMLElement).closest<HTMLElement>("article, section, li, tr");
    expect(article).not.toBeNull();
    const text = textOf(article as HTMLElement);
    expect(text).toMatch(/Niveau\s*3/);
    expect(text).toContain("Vu en jeu");
    expect(compact(article as HTMLElement)).toContain("3000");
    expect((screen.getByLabelText("Coût Or") as HTMLInputElement).value).toBe("4500");
    expect((screen.getByLabelText("Durée (secondes)") as HTMLInputElement).value).toBe("7200");
  });

  it("shows the API message of a refused correction and no Saisie manuelle", async () => {
    installFetch(
      levelWorld({
        status: 422,
        body: {
          detail: {
            code: "invalid_override",
            message: "La correction est invalide.",
            errors: ["duration_s: nombre négatif"],
          },
        },
      }),
    );
    const user = userEvent.setup();
    open(ROUTE);
    render(<App />);

    await screen.findByLabelText("Durée (secondes)");
    fireEvent.change(screen.getByLabelText("Durée (secondes)"), { target: { value: "5" } });
    await user.click(screen.getByRole("button", { name: "Enregistrer la correction" }));

    await expectAlert("La correction est invalide.");
    expect(bodyText()).not.toContain("Saisie manuelle");
  });
});
