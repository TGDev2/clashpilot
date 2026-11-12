// Sonde S05a (H05) : enregistrer la saisie manuelle ne doit pas effacer les événements du jeu.
// PUT /manual remplace tout l'état saisi : le formulaire doit renvoyer les `events`,
// `gold_pass` et `storage_override` déjà enregistrés qu'il n'affiche pas.
// A placer dans frontend/src/pages/import/ pour l'exécuter.
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ManualForm } from "./ManualForm";

const NOW = 1_800_000_000;
const EVENT = {
  label: "Défi des constructeurs",
  start: NOW,
  end: NOW + 7 * 86_400,
  lane: "builder",
  duration_pct: 80,
  cost_pct: 80,
};
const SAVED = {
  village_id: "P2Y8QQ0",
  resources: { gold: { value: 1000, status: "confirmed", as_of: NOW, source: "manual" } },
  builders_total: 5,
  magic_items: {},
  gold_pass: {},
  farm_rate: {},
  storage_override: { gold: 25_000_000 },
  events: [EVENT],
  source: "manual",
  updated_at: NOW,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

it("S05a keeps the saved events when the manual form is saved", async () => {
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
  expect(putBody?.events, "S05a events erased by the manual form").toEqual([EVENT]);
  expect(putBody?.storage_override, "S05a storage_override erased").toEqual({ gold: 25_000_000 });
});
