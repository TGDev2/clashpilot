/**
 * E12 acceptance tests: shell, import and village state (frozen with the exam commit).
 *
 * Contract of the UI these tests drive (everything is French, labels come from `fr.ts`):
 * - `App` is self-contained: it owns the router (BrowserRouter reading `window.location`) and sets
 *   `document.documentElement.lang = "fr"`. Tests move with `history.pushState` and nav clicks.
 * - Navigation: one or more `nav` landmarks holding the eight links of the contract.
 * - Importer page (`/importer`): a single `textarea` for the pasted export, a button "Importer mon
 *   village", a section `#packs` (heading "Données de jeu", one `input[type=file]`), a section
 *   `#saisie` (manual form), a button "Synchroniser avec l'API officielle" and a section whose
 *   heading is "Changements". Errors are shown in `role="alert"` regions.
 * - Dashboard (`/`): every item, resource and running upgrade is an `li`, `tr`, `article` or an
 *   element with role `row`/`listitem`. Resources are labelled Or, Élixir, Élixir noir, Gemmes,
 *   Minerai brillant, Minerai luisant, Minerai étoilé.
 * - Manual form labels: "Or" (value), "Statut Or", "Revenu par heure en bas Or", "Revenu par
 *   heure attendu Or", "Revenu par heure haut Or" (same for the other resources), "Nombre de
 *   constructeurs", "Nom de l'objet" and "Quantité" (one row, more with "Ajouter un objet"),
 *   submit button "Enregistrer la saisie". A blank field is an absent value, never 0.
 * - The current village is kept by the app (URL or localStorage): the tests import through the
 *   page, then move to other pages with the navigation.
 */
import { readFileSync } from "node:fs";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "../App";

// ---------------------------------------------------------------------------------- fetch stub

type Call = { method: string; path: string; search: string; headers: Headers; body: string };
type Reply = { status?: number; body?: unknown };
type Handler = (call: Call) => Reply | undefined;

const router = vi.hoisted(() => ({
  // Installed before any module loads, so a client that captured `fetch` at import still hits it.
  dispatch: (_input: unknown, _init?: unknown): Promise<Response> =>
    Promise.reject(new Error("fetch is not stubbed")),
}));
vi.hoisted(() => {
  globalThis.fetch = ((input: unknown, init?: unknown) =>
    router.dispatch(input, init)) as typeof fetch;
});

let calls: Call[] = [];
let handler: Handler = () => undefined;

async function toCall(input: unknown, init?: RequestInit): Promise<Call> {
  const request =
    input instanceof Request
      ? input
      : new Request(new URL(String(input), window.location.href), init);
  const url = new URL(request.url);
  return {
    method: request.method.toUpperCase(),
    path: url.pathname,
    search: url.search,
    headers: request.headers,
    body: await request.clone().text(),
  };
}

function installFetch(next: Handler): void {
  calls = [];
  handler = next;
  router.dispatch = async (input, init) => {
    const call = await toCall(input, init as RequestInit | undefined);
    calls.push(call);
    const reply = handler(call);
    if (reply === undefined) {
      const fallback = defaultReply(call);
      return json(fallback.status ?? 200, fallback.body);
    }
    return json(reply.status ?? 200, reply.body);
  };
  vi.stubGlobal("fetch", (input: unknown, init?: unknown) => router.dispatch(input, init));
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body ?? null), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function defaultReply(call: Call): Reply {
  if (call.method === "GET" && call.path === "/api/gamedata/packs") {
    return { body: [] };
  }
  return { status: 404, body: { detail: { code: "unhandled", message: "Route inconnue." } } };
}

function callsTo(method: string, path: string): Call[] {
  return calls.filter((c) => c.method === method && c.path === path);
}

// -------------------------------------------------------------------------------------- fixtures

const TAG = "P2Y8QQ0";
const NOW = Date.UTC(2026, 10, 12, 12, 0, 0) / 1000;
const ASOF = Date.UTC(2026, 10, 10, 12, 0, 0) / 1000;
const DAY = 86_400;
const HOUR = 3_600;
const MINUTE = 60;

const FAN_CONTENT_POLICY =
  "This material is unofficial and is not endorsed by Supercell. For more information see Supercell's Fan Content Policy: www.supercell.com/fan-content-policy.";

type Status = "confirmed" | "estimated" | "unknown";

function prov(value: number | null, status: Status, source = "export", asOf: number | null = ASOF) {
  return { value, status, source, as_of: asOf };
}

const RESOURCE_KEYS = [
  "gold",
  "elixir",
  "dark_elixir",
  "gems",
  "shiny_ore",
  "glowy_ore",
  "starry_ore",
] as const;

function unknownResources() {
  return Object.fromEntries(
    RESOURCE_KEYS.map((key) => [key, prov(null, "unknown", "manual", null)]),
  );
}

function item(
  dataId: number,
  name: string | null,
  level: number | null,
  status: Status = "confirmed",
  count = 1,
) {
  return {
    data_id: dataId,
    name,
    level,
    count,
    section: "buildings",
    village: "home",
    raw: null,
    provenance: prov(level, status, status === "confirmed" ? "export" : "model"),
  };
}

function job(dataId: number, name: string, finishAt: number, toLevel: number) {
  return {
    data_id: dataId,
    name,
    finish_at: finishAt,
    to_level: toLevel,
    section: "buildings",
    village: "home",
    extra: false,
    helper: false,
    provenance: prov(finishAt, "confirmed"),
  };
}

function villageState(overrides: Record<string, unknown> = {}) {
  return {
    village_id: TAG,
    snapshot_id: 1,
    tag: TAG,
    exported_at: ASOF,
    format_version: "fictional-2026-11",
    items: [],
    running: [],
    unknown_ids: [],
    unknown_sections: [],
    warnings: [],
    resources: unknownResources(),
    builders: prov(null, "unknown", "model", null),
    ...overrides,
  };
}

/** The state shown on the import page: 7 entries, 2 running upgrades, 3 unknown ids, 1 warning. */
const IMPORT_STATE = villageState({
  items: [
    item(101, "Canon d'essai", 5),
    item(102, "Mortier d'essai", 3),
    item(103, "Tour d'essai", 7),
    item(104, "Mine d'essai", 4),
    item(105, "Réservoir d'essai", 6),
    item(106, "Mur d'essai", 8),
    item(107, "Caserne d'essai", 2),
  ],
  running: [
    job(101, "Canon d'essai", NOW + DAY, 6),
    job(107, "Caserne d'essai", NOW + 2 * HOUR, 3),
  ],
  unknown_ids: [9001, 9002, 9003],
  warnings: ["Section « machines » ignorée : format inconnu."],
});

const EXPORT_JSON = JSON.stringify({
  tag: `#${TAG}`,
  timestamp: ASOF,
  buildings: [],
  fictional: 1,
});
const EXPORT_JSON_2 = JSON.stringify({
  tag: `#${TAG}`,
  timestamp: NOW,
  buildings: [],
  fictional: 2,
});

const PACK_A = {
  pack_id: "pack-alpha",
  game_version: "7.1-test",
  as_of: "2026-10-01",
  fictional: true,
  items: 12,
  levels: 90,
  active: true,
  sha256: "a".repeat(64),
  created_at: ASOF,
};
const PACK_B = {
  pack_id: "pack-beta",
  game_version: "8.2-reel",
  as_of: "2026-09-15",
  fictional: false,
  items: 20,
  levels: 140,
  active: false,
  sha256: "b".repeat(64),
  created_at: ASOF,
};

// ----------------------------------------------------------------------------------- helpers

function goTo(url: string): void {
  window.history.pushState({}, "", url);
}

function navLink(name: string): HTMLElement {
  const found = screen
    .getAllByRole("navigation")
    .flatMap((nav) => within(nav).queryAllByRole("link", { name }));
  const first = found[0];
  if (first === undefined) {
    throw new Error(`no navigation link named "${name}"`);
  }
  return first;
}

function rowOf(element: HTMLElement): HTMLElement {
  const row = element.closest<HTMLElement>("li, tr, article, [role='row'], [role='listitem']");
  if (row === null) {
    throw new Error(`"${element.textContent}" is not inside a row (li, tr, article, role row)`);
  }
  return row;
}

/** The rows (li, tr, article...) whose own label is exactly `label`. */
function rowsLabelled(label: string): HTMLElement[] {
  return screen.queryAllByText(label).flatMap((el) => {
    const row = el.closest<HTMLElement>("li, tr, article, [role='row'], [role='listitem']");
    return row === null ? [] : [row];
  });
}

function compact(text: string | null): string {
  return (text ?? "").replace(/[\s  .,]/g, "");
}

function hasBareZero(text: string | null): boolean {
  return /(^|[^\d])0([^\d]|$)/.test(text ?? "");
}

function countText(count: number, noun: string): RegExp {
  return new RegExp(`(\\b${count}\\s+${noun}|${noun}\\s*[:(]?\\s*${count}\\b)`, "i");
}

async function expectAlert(...needles: string[]): Promise<void> {
  await waitFor(() => {
    const alerts = screen.getAllByRole("alert");
    for (const needle of needles) {
      expect(alerts.some((a) => (a.textContent ?? "").includes(needle))).toBe(true);
    }
  });
}

function pasteExport(text: string): void {
  const area = document.querySelector("textarea");
  if (area === null) {
    throw new Error("no textarea to paste the export into");
  }
  // userEvent.paste needs a clipboard; a change event is what a paste produces.
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")?.set;
  setter?.call(area, text);
  area.dispatchEvent(new Event("input", { bubbles: true }));
}

async function importVillage(user: ReturnType<typeof userEvent.setup>, text = EXPORT_JSON) {
  pasteExport(text);
  await user.click(screen.getByRole("button", { name: "Importer mon village" }));
  await waitFor(() => expect(callsTo("POST", "/api/villages/import").length).toBeGreaterThan(0));
}

function villageHandlers(state: unknown): Handler {
  return (call) => {
    if (call.method === "POST" && call.path === "/api/villages/import") {
      return { status: 201, body: state };
    }
    if (call.method === "GET" && call.path === `/api/villages/${TAG}/state`) {
      return { body: state };
    }
    if (call.method === "GET" && call.path === `/api/villages/${TAG}/manual`) {
      return { body: emptyManual() };
    }
    return undefined;
  };
}

function emptyManual() {
  return {
    village_id: TAG,
    resources: {},
    builders_total: null,
    magic_items: {},
    gold_pass: {},
    farm_rate: {},
    storage_override: {},
    events: [],
    source: "manual",
    updated_at: null,
  };
}

function compose(...handlers: Handler[]): Handler {
  return (call) => {
    for (const next of handlers) {
      const reply = next(call);
      if (reply !== undefined) {
        return reply;
      }
    }
    return undefined;
  };
}

const ROUTES: ReadonlyArray<readonly [string, string]> = [
  ["Tableau de bord", "/"],
  ["Importer", "/importer"],
  ["Que faire maintenant ?", "/maintenant"],
  ["Calendrier", "/calendrier"],
  ["Constructeurs", "/constructeurs"],
  ["Scénarios", "/scenarios"],
  ["Historique", "/historique"],
  ["Réglages", "/reglages"],
];

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("lang");
  goTo("/");
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date(NOW * 1000));
  installFetch(() => undefined);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

// ------------------------------------------------------------------------------------ shell

describe("shell", () => {
  it("shows the eight navigation links, each leading to its route", async () => {
    const user = userEvent.setup();
    render(<App />);
    for (const [name] of ROUTES) {
      expect(navLink(name)).toBeTruthy();
    }
    for (const [name, route] of ROUTES) {
      expect(navLink(name).getAttribute("href")).toBe(route);
      await user.click(navLink(name));
      expect(window.location.pathname).toBe(route);
    }
  });

  it.each(ROUTES.map(([, route]) => route))(
    "shows the exact Fan Content Policy notice on %s",
    async (route) => {
      goTo(route);
      render(<App />);
      expect((await screen.findAllByText(FAN_CONTENT_POLICY)).length).toBeGreaterThan(0);
    },
  );

  it("sets the document language to French", () => {
    render(<App />);
    expect(document.documentElement.getAttribute("lang")).toBe("fr");
    const html = readFileSync(new URL("../../index.html", import.meta.url), "utf8");
    expect(html).toMatch(/<html[^>]*\blang="fr"/);
  });

  it.each(ROUTES)("opens %s with Tab then Enter", async (name, route) => {
    const user = userEvent.setup();
    render(<App />);
    let reached = false;
    for (let presses = 0; presses < 60 && !reached; presses++) {
      await user.tab();
      const active = document.activeElement;
      reached = active === navLink(name);
    }
    expect(reached).toBe(true);
    await user.keyboard("{Enter}");
    expect(window.location.pathname).toBe(route);
  });
});

// --------------------------------------------------------------------------------- import page

describe("import page", () => {
  it("posts the pasted export and shows the counts and warnings", async () => {
    installFetch(villageHandlers(IMPORT_STATE));
    const user = userEvent.setup();
    goTo("/importer");
    render(<App />);
    await importVillage(user);

    const posts = callsTo("POST", "/api/villages/import");
    expect(posts).toHaveLength(1);
    expect(JSON.parse(posts[0]?.body ?? "null")).toEqual(JSON.parse(EXPORT_JSON));

    await waitFor(() => {
      expect(document.body.textContent ?? "").toMatch(countText(7, "éléments?"));
    });
    const shown = document.body.textContent ?? "";
    expect(shown).toMatch(countText(2, "améliorations?\\s+en\\s+cours"));
    expect(shown).toMatch(countText(3, "identifiants?\\s+inconnus?"));
    expect(shown).toContain("Section « machines » ignorée : format inconnu.");
  });

  it("shows the French message of a 422 in an alert", async () => {
    const message = "Ce contenu n'est pas un export de village.";
    installFetch((call) =>
      call.method === "POST" && call.path === "/api/villages/import"
        ? { status: 422, body: { detail: { code: "invalid_export", message } } }
        : undefined,
    );
    const user = userEvent.setup();
    goTo("/importer");
    render(<App />);
    await importVillage(user, '{"pas": "un export"}');
    await expectAlert(message);
  });
});

// ----------------------------------------------------------------------------------- dashboard

const DASHBOARD_STATE = villageState({
  items: [
    item(101, "Canon d'essai", 5, "confirmed", 2),
    item(102, "Mortier d'essai", 3, "confirmed"),
    item(103, "Tour d'essai", 7, "estimated"),
    item(104, "Piège d'essai", null, "unknown"),
    item(105, "Laboratoire d'essai", 4, "confirmed"),
  ],
  running: [
    job(101, "Canon d'essai", NOW + 2 * DAY + 3 * HOUR + 15 * MINUTE, 6),
    job(105, "Laboratoire d'essai", NOW + DAY + 5 * HOUR + 7 * MINUTE, 5),
  ],
  resources: {
    ...unknownResources(),
    gold: prov(1234567, "confirmed", "manual"),
    elixir: prov(89012, "estimated", "manual"),
    dark_elixir: prov(null, "unknown", "manual", null),
  },
});

async function openDashboard(packs: unknown[], state: unknown = DASHBOARD_STATE) {
  installFetch(
    compose(villageHandlers(state), (call) =>
      call.method === "GET" && call.path === "/api/gamedata/packs" ? { body: packs } : undefined,
    ),
  );
  const user = userEvent.setup();
  goTo("/importer");
  render(<App />);
  await importVillage(user);
  await user.click(navLink("Tableau de bord"));
  await screen.findAllByText("Canon d'essai");
  return user;
}

describe("dashboard", () => {
  it("gives each item and resource a badge from its provenance status", async () => {
    await openDashboard([PACK_B]);

    const canon = rowsLabelled("Canon d'essai");
    expect(canon.some((r) => /Confirmé/.test(r.textContent ?? ""))).toBe(true);
    const tower = rowsLabelled("Tour d'essai");
    expect(tower.some((r) => /Estimé/.test(r.textContent ?? ""))).toBe(true);
    const trap = rowsLabelled("Piège d'essai");
    expect(trap.some((r) => /Inconnu/.test(r.textContent ?? ""))).toBe(true);

    const [gold] = rowsLabelled("Or");
    const [elixir] = rowsLabelled("Élixir");
    expect(gold?.textContent).toMatch(/Confirmé/);
    expect(compact(gold?.textContent ?? "")).toContain("1234567");
    expect(elixir?.textContent).toMatch(/Estimé/);
    expect(compact(elixir?.textContent ?? "")).toContain("89012");
  });

  it("shows Inconnu and never 0 for an unknown resource or level", async () => {
    await openDashboard([PACK_B]);

    for (const label of ["Élixir noir", "Gemmes", "Minerai brillant", "Minerai luisant"]) {
      const rows = rowsLabelled(label);
      expect(rows.length, `row of ${label}`).toBeGreaterThan(0);
      for (const row of rows) {
        expect(row.textContent).toMatch(/Inconnu/);
        expect(row.textContent).not.toMatch(/Confirmé|Estimé/);
        expect(hasBareZero(row.textContent)).toBe(false);
      }
    }
    for (const row of rowsLabelled("Piège d'essai")) {
      expect(hasBareZero(row.textContent)).toBe(false);
    }
  });

  it("shows the time left of a running upgrade in days, hours and minutes", async () => {
    await openDashboard([PACK_B]);

    const rows = [...document.querySelectorAll<HTMLElement>("li, tr, article, [role]")];
    const timeOf = (name: string, time: RegExp) =>
      rows.some((r) => (r.textContent ?? "").includes(name) && time.test(r.textContent ?? ""));
    expect(timeOf("Canon d'essai", /2\s*j(ours?)?\b.*3\s*h(eures?)?\b.*15\s*min/i)).toBe(true);
    expect(timeOf("Laboratoire d'essai", /1\s*j(ours?)?\b.*5\s*h(eures?)?\b.*7\s*min/i)).toBe(true);
  });

  it("shows the Données fictives banner when the active pack is fictional", async () => {
    await openDashboard([PACK_A, PACK_B]);
    expect(screen.getAllByText(/Données fictives/).length).toBeGreaterThan(0);
  });

  it("hides the banner when the active pack is not fictional", async () => {
    await openDashboard([{ ...PACK_B, active: true }]);
    await waitFor(() => expect(callsTo("GET", "/api/gamedata/packs").length).toBeGreaterThan(0));
    expect(screen.queryByText(/Données fictives/)).toBeNull();
  });

  it("invites to activate a pack, linking to /importer#packs, when none is active", async () => {
    await openDashboard([{ ...PACK_A, active: false }, PACK_B]);
    await waitFor(() => expect(document.querySelector('a[href="/importer#packs"]')).not.toBeNull());
    expect(document.body.textContent).toMatch(/pack/i);
    expect(document.body.textContent).toMatch(/activ/i);
    expect(screen.queryByText(/Données fictives/)).toBeNull();
  });

  it("does not invite to activate a pack when one is active", async () => {
    await openDashboard([{ ...PACK_B, active: true }]);
    await waitFor(() => expect(callsTo("GET", "/api/gamedata/packs").length).toBeGreaterThan(0));
    expect(document.querySelector('a[href="/importer#packs"]')).toBeNull();
  });
});

// ------------------------------------------------------------------------------- packs section

function packsHandler(initial: Array<Record<string, unknown>>): Handler {
  let packs = initial;
  return (call) => {
    if (call.path === "/api/gamedata/packs" && call.method === "GET") {
      return { body: packs };
    }
    if (call.path === "/api/gamedata/packs" && call.method === "POST") {
      const sent = JSON.parse(call.body) as { pack_id: string };
      if (sent.pack_id === "pack-invalide") {
        return {
          status: 422,
          body: {
            detail: {
              code: "invalid_pack",
              message: "Le pack de données est invalide.",
              errors: ["items.0.name: champ requis", "levels.3.source: source absente"],
            },
          },
        };
      }
      const created = { ...PACK_B, pack_id: sent.pack_id, game_version: "9.0-neuf", active: false };
      packs = [...packs, created];
      return { status: 201, body: created };
    }
    const activate = call.path.match(/^\/api\/gamedata\/packs\/([^/]+)\/active$/);
    if (activate !== null && call.method === "PUT") {
      packs = packs.map((p) => ({ ...p, active: p.pack_id === activate[1] }));
      return { body: packs.find((p) => p.pack_id === activate[1]) };
    }
    return undefined;
  };
}

function packsSection(): HTMLElement {
  const section = document.getElementById("packs");
  if (section === null) {
    throw new Error("no element with id packs on the import page");
  }
  return section;
}

async function sendPack(user: ReturnType<typeof userEvent.setup>, pack: unknown): Promise<void> {
  const input = packsSection().querySelector<HTMLInputElement>('input[type="file"]');
  if (input === null) {
    throw new Error("no file input in the packs section");
  }
  const file = new File([JSON.stringify(pack)], "pack.json", { type: "application/json" });
  await user.upload(input, file);
  try {
    await waitFor(() => expect(callsTo("POST", "/api/gamedata/packs").length).toBe(1), {
      timeout: 400,
    });
  } catch {
    // The page may ask for a confirmation click once a file is chosen.
    const button = within(packsSection()).getByRole("button", {
      name: /importer|envoyer|téléverser|charger|ajouter/i,
    });
    await user.click(button);
  }
  await waitFor(() => expect(callsTo("POST", "/api/gamedata/packs").length).toBe(1));
}

describe("game data section", () => {
  it("lists the packs with version, date, Fictif and Actif", async () => {
    installFetch(packsHandler([PACK_A, PACK_B]));
    goTo("/importer");
    render(<App />);

    const alpha = await waitFor(() => {
      const el = within(packsSection()).getByText(/pack-alpha/);
      return rowOf(el);
    });
    const beta = rowOf(within(packsSection()).getByText(/pack-beta/));
    expect(alpha.textContent).toContain("7.1-test");
    expect(alpha.textContent).toMatch(/2026-10-01|01\/10\/2026|1 octobre 2026|01 octobre 2026/);
    expect(alpha.textContent).toMatch(/\bFictif\b/);
    expect(alpha.textContent).toMatch(/\bActif\b/);
    expect(beta.textContent).toContain("8.2-reel");
    expect(beta.textContent).toMatch(/2026-09-15|15\/09\/2026|15 septembre 2026/);
    expect(beta.textContent).not.toMatch(/\bFictif\b/);
    expect(beta.textContent).not.toMatch(/\bActif\b/);
  });

  it("sends the chosen pack file to POST /api/gamedata/packs and lists it", async () => {
    installFetch(packsHandler([PACK_A]));
    const user = userEvent.setup();
    goTo("/importer");
    render(<App />);
    await within(packsSection()).findByText(/pack-alpha/);

    const pack = { pack_id: "pack-gamma", data: { game_version: "9.0-neuf" } };
    await sendPack(user, pack);
    expect(JSON.parse(callsTo("POST", "/api/gamedata/packs")[0]?.body ?? "null")).toEqual(pack);
    expect(await within(packsSection()).findByText(/pack-gamma/)).toBeTruthy();
  });

  it("activates a pack with PUT and shows it as Actif", async () => {
    installFetch(packsHandler([PACK_A, PACK_B]));
    const user = userEvent.setup();
    goTo("/importer");
    render(<App />);
    const beta = await waitFor(() => rowOf(within(packsSection()).getByText(/pack-beta/)));

    await user.click(within(beta).getByRole("button", { name: "Activer" }));
    await waitFor(() =>
      expect(callsTo("PUT", "/api/gamedata/packs/pack-beta/active")).toHaveLength(1),
    );
    await waitFor(() => {
      const row = rowOf(within(packsSection()).getByText(/pack-beta/));
      expect(row.textContent).toMatch(/\bActif\b/);
    });
    const alpha = rowOf(within(packsSection()).getByText(/pack-alpha/));
    expect(alpha.textContent).not.toMatch(/\bActif\b/);
  });

  it("shows detail.errors of an invalid pack in an alert", async () => {
    installFetch(packsHandler([PACK_A]));
    const user = userEvent.setup();
    goTo("/importer");
    render(<App />);
    await within(packsSection()).findByText(/pack-alpha/);

    await sendPack(user, { pack_id: "pack-invalide" });
    await expectAlert("items.0.name: champ requis", "levels.3.source: source absente");
  });
});

// ------------------------------------------------------------------------------- manual input

const MANUAL_RESPONSE = {
  village_id: TAG,
  resources: {
    gold: { value: 9999, status: "estimated", as_of: ASOF, source: "manual" },
    gems: { value: 41, status: "confirmed", as_of: ASOF, source: "manual" },
  },
  builders_total: 6,
  magic_items: { builder_potion: 3, research_potion: 1 },
  gold_pass: {},
  farm_rate: { gold: { low: 100, expected: 200, high: 300 } },
  storage_override: {},
  events: [],
  source: "manual",
  updated_at: ASOF,
};

function saisie(): HTMLElement {
  const section = document.getElementById("saisie");
  if (section === null) {
    throw new Error("no element with id saisie on the import page");
  }
  return section;
}

async function typeInto(
  user: ReturnType<typeof userEvent.setup>,
  label: string,
  value: string,
  index = 0,
): Promise<void> {
  const field = within(saisie()).getAllByLabelText(label)[index];
  if (field === undefined) {
    throw new Error(`no field labelled "${label}" (#${index})`);
  }
  await user.clear(field);
  await user.type(field, value);
}

describe("manual input form", () => {
  it("starts blank, sends PUT /manual with what was typed and shows the returned values", async () => {
    installFetch(
      compose(villageHandlers(IMPORT_STATE), (call) =>
        call.method === "PUT" && call.path === `/api/villages/${TAG}/manual`
          ? { body: MANUAL_RESPONSE }
          : undefined,
      ),
    );
    const user = userEvent.setup();
    goTo("/importer");
    render(<App />);
    await importVillage(user);
    await within(saisie()).findByLabelText("Or");

    expect((within(saisie()).getByLabelText("Or") as HTMLInputElement).value).toBe("");
    expect(
      (within(saisie()).getByLabelText("Nombre de constructeurs") as HTMLInputElement).value,
    ).toBe("");

    await typeInto(user, "Or", "5000");
    await user.selectOptions(within(saisie()).getByLabelText("Statut Or"), "Estimé");
    await typeInto(user, "Gemmes", "40");
    await user.selectOptions(within(saisie()).getByLabelText("Statut Gemmes"), "Confirmé");
    await typeInto(user, "Nombre de constructeurs", "6");
    await typeInto(user, "Revenu par heure en bas Or", "100");
    await typeInto(user, "Revenu par heure attendu Or", "200");
    await typeInto(user, "Revenu par heure haut Or", "300");
    await typeInto(user, "Nom de l'objet", "builder_potion");
    await typeInto(user, "Quantité", "3");
    await user.click(within(saisie()).getByRole("button", { name: "Ajouter un objet" }));
    await typeInto(user, "Nom de l'objet", "research_potion", 1);
    await typeInto(user, "Quantité", "1", 1);
    await user.click(within(saisie()).getByRole("button", { name: "Enregistrer la saisie" }));

    await waitFor(() => expect(callsTo("PUT", `/api/villages/${TAG}/manual`)).toHaveLength(1));
    const body = JSON.parse(callsTo("PUT", `/api/villages/${TAG}/manual`)[0]?.body ?? "null");
    expect(body).toMatchObject({
      resources: {
        gold: { value: 5000, status: "estimated", as_of: NOW },
        gems: { value: 40, status: "confirmed", as_of: NOW },
      },
      builders_total: 6,
      farm_rate: { gold: { low: 100, expected: 200, high: 300 } },
      magic_items: { builder_potion: 3, research_potion: 1 },
    });
    // A blank field is an absent value: no resource is sent as 0.
    expect(Object.keys(body.resources).sort()).toEqual(["gems", "gold"]);
    expect(Object.keys(body.farm_rate ?? {})).toEqual(["gold"]);

    await waitFor(() => expect(compact(saisie().textContent)).toContain("9999"));
    expect(saisie().textContent).toMatch(/10[/.-]11[/.-]2026|2026-11-10|10 novembre 2026/);
  });
});

// ------------------------------------------------------------------------------------- sync

const SYNC_RESPONSE = {
  village_id: TAG,
  updated: [
    {
      data_id: 201,
      name: "Barbare d'essai",
      section: "troops",
      from_level: 4,
      level: 5,
      provenance: prov(5, "confirmed", "api", NOW),
    },
    {
      data_id: 202,
      name: "Archère d'essai",
      section: "troops",
      from_level: 3,
      level: 4,
      provenance: prov(4, "confirmed", "api", NOW),
    },
  ],
  conflicts: [
    {
      name: "Roi d'essai",
      section: "heroes",
      level: 12,
      reason: "Niveau différent entre l'API et l'export.",
    },
  ],
  fetched_at: NOW,
};

describe("official API synchronisation", () => {
  it("posts to /sync and shows the updated count and the conflicts", async () => {
    installFetch(
      compose(villageHandlers(IMPORT_STATE), (call) =>
        call.method === "POST" && call.path === `/api/villages/${TAG}/sync`
          ? { body: SYNC_RESPONSE }
          : undefined,
      ),
    );
    const user = userEvent.setup();
    goTo("/importer");
    render(<App />);
    await importVillage(user);
    await user.click(
      await screen.findByRole("button", { name: "Synchroniser avec l'API officielle" }),
    );

    await waitFor(() => expect(callsTo("POST", `/api/villages/${TAG}/sync`)).toHaveLength(1));
    await waitFor(() => {
      expect(document.body.textContent).toMatch(
        /\b2\s+(éléments?\s+)?mis à jour|mis à jour\s*[:(]?\s*2\b/i,
      );
    });
    expect(document.body.textContent).toContain("Roi d'essai");
    expect(document.body.textContent).toContain("Niveau différent entre l'API et l'export.");
  });

  it("asks for no Supercell identifier and sends no credential", async () => {
    installFetch(
      compose(villageHandlers(IMPORT_STATE), (call) =>
        call.method === "POST" && call.path === `/api/villages/${TAG}/sync`
          ? { body: SYNC_RESPONSE }
          : undefined,
      ),
    );
    const user = userEvent.setup();
    goTo("/importer");
    render(<App />);
    const forbidden = /identifiant|supercell|mot de passe|password|jeton|token|e-?mail|clé d'api/i;
    const fieldsAreClean = () => {
      for (const input of document.querySelectorAll<HTMLInputElement>("input, textarea")) {
        const labels = [...(input.labels ?? [])].map((l) => l.textContent ?? "").join(" ");
        const described = `${labels} ${input.name} ${input.id} ${input.placeholder} ${input.getAttribute("aria-label") ?? ""}`;
        expect(input.type).not.toBe("password");
        expect(described).not.toMatch(forbidden);
      }
    };
    fieldsAreClean();
    await importVillage(user);
    await user.click(
      await screen.findByRole("button", { name: "Synchroniser avec l'API officielle" }),
    );
    await waitFor(() => expect(callsTo("POST", `/api/villages/${TAG}/sync`)).toHaveLength(1));
    fieldsAreClean();
    const sync = callsTo("POST", `/api/villages/${TAG}/sync`)[0];
    expect(sync?.body).toBe("");
    expect(sync?.headers.get("authorization")).toBeNull();
  });

  it.each([
    [409, "api_not_configured", "La clé de l'API officielle n'est pas configurée."],
    [502, "coc_access_denied", "L'accès à l'API officielle a été refusé."],
    [404, "coc_player_not_found", "Ce joueur est introuvable dans l'API officielle."],
    [503, "coc_rate_limited", "L'API officielle limite les requêtes, réessayez plus tard."],
    [503, "coc_unavailable", "L'API officielle est indisponible."],
  ])("shows the message of %i %s in an alert", async (status, code, message) => {
    installFetch(
      compose(villageHandlers(IMPORT_STATE), (call) =>
        call.method === "POST" && call.path === `/api/villages/${TAG}/sync`
          ? { status, body: { detail: { code, message } } }
          : undefined,
      ),
    );
    const user = userEvent.setup();
    goTo("/importer");
    render(<App />);
    await importVillage(user);
    await user.click(
      await screen.findByRole("button", { name: "Synchroniser avec l'API officielle" }),
    );
    await expectAlert(message);
  });
});

// ----------------------------------------------------------------------------------- changes

describe("changes section", () => {
  it("shows the upgrades returned by the diff API after a second import", async () => {
    let imports = 0;
    installFetch(
      compose((call) => {
        if (call.method === "POST" && call.path === "/api/villages/import") {
          imports += 1;
          return { status: 201, body: { ...IMPORT_STATE, snapshot_id: imports } };
        }
        if (call.method === "GET" && call.path === `/api/villages/${TAG}/diff`) {
          return imports < 2
            ? {
                status: 409,
                body: {
                  detail: {
                    code: "diff_requires_two_snapshots",
                    message: "Il faut au moins deux imports de ce village pour les comparer.",
                  },
                },
              }
            : {
                body: {
                  village_id: TAG,
                  from_snapshot_id: 1,
                  to_snapshot_id: 2,
                  upgraded: [
                    { data_id: 101, name: "Canon d'essai", from_level: 4, to_level: 5, count: 1 },
                  ],
                  added: [],
                  removed: [],
                  started: [],
                  finished: [],
                },
              };
        }
        return undefined;
      }, villageHandlers(IMPORT_STATE)),
    );
    const user = userEvent.setup();
    goTo("/importer");
    render(<App />);
    await importVillage(user);
    pasteExport(EXPORT_JSON_2);
    await user.click(screen.getByRole("button", { name: "Importer mon village" }));
    await waitFor(() => expect(callsTo("POST", "/api/villages/import")).toHaveLength(2));

    const heading = await screen.findByRole("heading", { name: "Changements" });
    const section = heading.closest("section") ?? heading.parentElement;
    expect(section).not.toBeNull();
    await waitFor(() => {
      const text = section?.textContent ?? "";
      expect(text).toContain("Canon d'essai");
      expect(text).toMatch(/\b4\b\D+\b5\b/);
    });
    expect(callsTo("GET", `/api/villages/${TAG}/diff`).length).toBeGreaterThan(0);
  });
});
