// PUT /manual replaces the whole typed-in state: saving the home form must send back the builder
// base inputs it does not show (builder count, builder resources and rates, the village of each
// event).
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ManualForm } from "./ManualForm";

const NOW = 1_800_000_000;
const HOME_EVENT = {
  label: "Défi des constructeurs",
  start: NOW,
  end: NOW + 86_400,
  lane: "builder",
  duration_pct: 80,
  cost_pct: 100,
  base: "home",
};
const BUILDER_EVENT = { ...HOME_EVENT, label: "Fête des artisans", lane: "lab", base: "builder" };
const SAVED = {
  village_id: "P2Y8QQ0",
  resources: {
    gold: { value: 1000, status: "confirmed", as_of: NOW, source: "manual" },
    builder_gold: { value: 700, status: "estimated", as_of: NOW, source: "manual" },
  },
  builders_total: 5,
  builder_base_builders: 3,
  magic_items: {},
  gold_pass: {},
  farm_rate: { builder_elixir: { low: 1, expected: 2, high: 3 } },
  storage_override: {},
  events: [HOME_EVENT, BUILDER_EVENT],
  source: "manual",
  updated_at: NOW,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

it("keeps the builder base inputs when the manual form is saved", async () => {
  let putBody: Record<string, unknown> | undefined;
  vi.stubGlobal("fetch", async (input: Request | string) => {
    const request = input instanceof Request ? input : new Request(new URL(input, "http://x"));
    if (request.method === "PUT") {
      putBody = (await request.clone().json()) as Record<string, unknown>;
      return Response.json({ ...SAVED, ...putBody, updated_at: NOW + 60 });
    }
    return Response.json(SAVED);
  });
  render(<ManualForm villageId="P2Y8QQ0" />);
  await screen.findByText("Valeurs enregistrées");
  await userEvent.click(screen.getByRole("button", { name: "Enregistrer la saisie" }));
  await waitFor(() => expect(putBody).toBeDefined());

  expect(putBody?.builder_base_builders).toBe(3);
  expect(putBody?.events).toEqual([HOME_EVENT, BUILDER_EVENT]);
  expect(putBody?.resources).toMatchObject({
    gold: { value: 1000, status: "confirmed" },
    builder_gold: { value: 700, status: "estimated", as_of: NOW },
  });
  expect(putBody?.resources).not.toHaveProperty("builder_gold.source");
  expect(putBody?.farm_rate).toEqual({ builder_elixir: { low: 1, expected: 2, high: 3 } });
});
