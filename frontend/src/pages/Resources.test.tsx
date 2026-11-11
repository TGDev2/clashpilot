import { render, screen, within } from "@testing-library/react";
import { App } from "../App";

const NOW = 1_800_000_000;
const known = (value: number) => ({ value, status: "estimated", source: "model", as_of: NOW });
const unknown = { value: null, status: "unknown", source: "model", as_of: null };
const triple = (estimate: unknown) => ({ low: estimate, expected: estimate, high: estimate });

// What the real API sends without a farm rate: the stock now is known, the later points are not.
const FORECAST = {
  plan_id: 1,
  now: NOW,
  horizon_end: NOW + 7_200,
  step_s: 3_600,
  series: {
    gold: [
      { t: NOW, ...triple(known(5_000)) },
      { t: NOW + 3_600, ...triple(unknown) },
    ],
    elixir: [
      { t: NOW, ...triple(known(7_000)) },
      { t: NOW + 3_600, ...triple(known(7_500)) },
    ],
  },
  funding: [],
  saturation: [],
  blocking: null,
  walls: [],
  effort: { scope: "plan", cost: {}, farm_hours: {} },
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Ressources avec un taux de collecte partiel", () => {
  it("links to the manual input and keeps Estimé off a resource holding an unknown value", async () => {
    vi.stubGlobal("fetch", async (input: Request | string) => {
      const path = new URL(input instanceof Request ? input.url : input, "http://x").pathname;
      return path.endsWith("/forecast")
        ? Response.json(FORECAST)
        : Response.json(
            { detail: { code: "not_found", message: "Introuvable." } },
            { status: 404 },
          );
    });
    localStorage.setItem("clashpilot.village", "P2Y8QQ0");
    window.history.pushState({}, "", "/ressources");
    render(<App />);

    const gold = (await screen.findByRole("heading", { name: "Or" })).closest("section");
    const elixir = (await screen.findByRole("heading", { name: "Élixir" })).closest("section");
    expect(gold?.textContent).toContain("Inconnu");
    expect(gold?.textContent).not.toContain("Estimé");
    expect(elixir?.textContent).toContain("Estimé");
    expect(elixir?.textContent).not.toContain("Inconnu");
    expect(document.querySelector('a[href="/importer#saisie"]')).not.toBeNull();
    expect(within(gold as HTMLElement).getAllByRole("row")).toHaveLength(3);
    // One known point is no line: no empty chart for gold, a drawn one for elixir.
    expect(gold?.querySelector("svg [data-series]")).toBeNull();
    expect(gold?.querySelector("svg")).toBeNull();
    expect(elixir?.querySelector('svg [data-series="expected"]')).not.toBeNull();
  });
});
