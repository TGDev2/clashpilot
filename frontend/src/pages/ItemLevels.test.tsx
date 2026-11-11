import { render, screen } from "@testing-library/react";
import { App } from "../App";

const level = (n: number, original: unknown = null) => ({
  level: n,
  duration_s: 60,
  cost: { gold: 10 },
  requires: [],
  source: { id: "s", label: "Source", url: null, retrieved_at: "2026-01-01", license: null },
  original,
  note: null,
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Niveaux de l'objet", () => {
  it("links each level to its sheet and marks a corrected one", async () => {
    vi.stubGlobal("fetch", async () =>
      Response.json({
        pack_id: "pack-alpha",
        data_id: 1000001,
        name: "Canon d'essai",
        kind: "building",
        lane: "builder",
        category: "defense",
        tags: [],
        max_level: 2,
        levels: [level(1), level(2, { duration_s: 30, cost: { gold: 5 } })],
      }),
    );
    window.history.pushState({}, "", "/donnees/pack-alpha/1000001");
    render(<App />);

    const second = await screen.findByRole("link", { name: "Niveau 2" });
    expect(second.getAttribute("href")).toBe("/donnees/pack-alpha/1000001/2");
    expect(screen.getByRole("link", { name: "Niveau 1" })).toBeTruthy();
    expect(screen.getAllByText("Saisie manuelle")).toHaveLength(1);
  });
});
