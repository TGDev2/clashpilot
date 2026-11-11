import { readFileSync } from "node:fs";
import { join } from "node:path";
import { type APIRequestContext, expect, type Page, test } from "@playwright/test";

// The player's journey on the built application, with the fictional pack and village of
// `e2e/fixtures`. The scenarios share one village and run in order.
test.describe.configure({ mode: "serial" });

const VILLAGE = "FICTIF09";
const API = `/api/villages/${VILLAGE}`;

// Each test opens a fresh browser context: the import of the first one is remembered like a reload.
test.beforeEach(async ({ page }) => {
  await page.addInitScript((id) => localStorage.setItem("clashpilot.village", id), VILLAGE);
});

function fixture(name: string): Record<string, unknown> {
  return JSON.parse(readFileSync(join(import.meta.dirname, "fixtures", name), "utf8"));
}

type Assignment = { task_id: string; name: string; status: string; start: number; end: number };
type Plan = { assignments: Assignment[]; timezone: string };
type Action = { action: string; at: number; task_id: string | null };

async function latestPlan(request: APIRequestContext): Promise<Plan> {
  const response = await request.get(`${API}/plans/latest`);
  expect(response.status()).toBe(200);
  return (await response.json()) as Plan;
}

/** The export and the manual input of the fixtures, dated a minute ago. */
function datedFixtures() {
  const now = Math.floor(Date.now() / 1000);
  const manual = fixture("manual.json") as { resources: Record<string, { as_of: number }> };
  for (const resource of Object.values(manual.resources)) {
    resource.as_of = now - 60;
  }
  return { exportText: JSON.stringify({ ...fixture("export.json"), timestamp: now - 60 }), manual };
}

/** No horizontal scrolling: the page is not wider than the window. */
async function expectNoHorizontalScroll(page: Page, route: string): Promise<void> {
  await page.goto(route);
  await expect(page.getByRole("heading", { level: 2 }).first()).toBeVisible();
  const { scrollWidth, clientWidth } = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(scrollWidth, `${route}: scrollWidth ${scrollWidth} > clientWidth ${clientWidth}`).toBeLessThanOrEqual(
    clientWidth,
  );
}

test("import du pack fictif et de l'export du village", async ({ page, request }) => {
  const pack = fixture("pack.json");
  expect((await request.post("/api/gamedata/packs", { data: pack })).ok()).toBe(true);
  expect((await request.put(`/api/gamedata/packs/${pack.pack_id}/active`)).ok()).toBe(true);
  const { exportText, manual } = datedFixtures();

  await page.goto("/importer");
  await page.getByLabel("Export du village (JSON)").fill(exportText);
  await page.getByRole("button", { name: "Importer mon village" }).click();
  await expect(page.getByRole("heading", { name: "Village importé" })).toBeVisible();
  await expect(page.getByText("7 éléments")).toBeVisible();
  await expect(page.getByText("1 amélioration en cours")).toBeVisible();

  // The manual input and the schedule come from the API: their forms are covered by Vitest.
  expect((await request.put(`${API}/manual`, { data: manual })).ok()).toBe(true);
  expect((await request.put(`${API}/schedule`, { data: fixture("schedule.json") })).ok()).toBe(true);
});

test("que faire maintenant : calcul du plan et actions à lancer", async ({ page }) => {
  await page.goto("/maintenant");
  await page.getByRole("button", { name: "Calculer mon plan" }).click();

  await expect(page.getByText("Plan calculé.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Actions à lancer" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Je l'ai lancée" }).first()).toBeVisible();
  await expect(page.getByText("Données fictives", { exact: false }).first()).toBeVisible();
});

test("calendrier : les fins de la semaine", async ({ page, request }) => {
  const plan = await latestPlan(request);
  const dayOf = new Intl.DateTimeFormat("en-CA", { timeZone: plan.timezone });

  await page.goto("/calendrier");

  await expect(page.getByRole("heading", { name: "Calendrier" })).toBeVisible();
  const days = await page.locator("[data-day]").evaluateAll((nodes) =>
    nodes.map((node) => node.getAttribute("data-day")),
  );
  expect(days).toHaveLength(7);
  const thisWeek = plan.assignments.filter((a) => days.includes(dayOf.format(a.end * 1000)));
  expect(thisWeek.length, "the plan ends something this week").toBeGreaterThan(0);
  for (const assignment of thisWeek) {
    const column = page.locator(`[data-day="${dayOf.format(assignment.end * 1000)}"]`);
    await expect(column).toContainText(assignment.name);
  }
});

test("Gantt : une barre par amélioration du plan", async ({ page, request }) => {
  const plan = await latestPlan(request);

  await page.goto("/constructeurs");

  await expect(page.getByRole("heading", { name: "Constructeurs" })).toBeVisible();
  await expect(page.locator("[data-lane]").first()).toBeVisible();
  for (const assignment of plan.assignments) {
    await expect(page.locator(`[data-task-id="${assignment.task_id}"]`).first()).toBeAttached();
  }
});

test("retard d'une heure : simulation, confirmation et journal", async ({ page, request }) => {
  await page.goto("/maintenant");
  await expect(page.getByRole("button", { name: "Je l'ai lancée" }).first()).toBeVisible();
  const answer = (await (await request.get(`${API}/recommendations/now`)).json()) as {
    now: number;
    actions: Action[];
  };
  const first = answer.actions.find((action) => action.action === "start");
  expect(first, "a start action").toBeDefined();
  const planned = first?.at ?? 0;

  // The server refuses an event dated in the future: let the hour of delay go by first.
  const wait = Math.max(0, planned + 3660 - answer.now);
  expect((await request.post(`/__e2e/advance?seconds=${wait}`)).ok()).toBe(true);

  await page.getByRole("button", { name: "Simuler un retard" }).first().click();
  await page.getByRole("button", { name: "1 h" }).first().click();
  await expect(page.getByRole("heading", { name: "Ce qui change" })).toBeVisible();
  const before = (await (await request.get(`${API}/journal`)).json()) as unknown[];
  expect(before, "a simulation records nothing").toHaveLength(0);

  await page.getByRole("button", { name: "Confirmer" }).click();
  await expect(page.getByRole("heading", { name: "Conséquences" })).toBeVisible();
  const journal = (await (await request.get(`${API}/journal`)).json()) as {
    type: string;
    at: number;
    task_id: string | null;
  }[];
  expect(journal).toHaveLength(1);
  expect(journal[0]).toMatchObject({ type: "task_started", at: planned + 3600, task_id: first?.task_id });
});

test("viewport téléphone : aucun défilement horizontal à 390 px", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });

  for (const route of [
    "/",
    "/importer",
    "/maintenant",
    "/calendrier",
    "/constructeurs",
    "/reglages",
    "/historique",
  ]) {
    await expectNoHorizontalScroll(page, route);
  }
});
