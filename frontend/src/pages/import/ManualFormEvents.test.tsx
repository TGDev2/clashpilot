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

it("sends a game event typed in the form as percentages of the base value", async () => {
  let putBody: Record<string, unknown> | undefined;
  vi.stubGlobal("fetch", async (input: Request | string) => {
    const request = input instanceof Request ? input : new Request(new URL(input, "http://x"));
    if (request.method === "PUT") {
      putBody = (await request.clone().json()) as Record<string, unknown>;
      return Response.json({ ...SAVED, ...putBody, updated_at: NOW + 60 });
    }
    return Response.json({ ...SAVED, events: [] });
  });
  const user = userEvent.setup();
  render(<ManualForm villageId="P2Y8QQ0" />);
  await screen.findByText("Valeurs enregistrées");
  await user.click(screen.getByRole("button", { name: "Ajouter un événement" }));
  await user.type(screen.getByLabelText("Nom de l'événement"), "Fête des bâtisseurs");
  await user.selectOptions(screen.getByLabelText("File concernée"), "lab");
  await user.type(screen.getByLabelText("Début"), "2027-01-10T08:00");
  await user.type(screen.getByLabelText("Fin"), "2027-01-17T08:00");
  await user.type(screen.getByLabelText("Réduction de durée (%)"), "20");
  await user.type(screen.getByLabelText("Réduction de coût (%)"), "50");
  await user.click(screen.getByRole("button", { name: "Enregistrer la saisie" }));
  await waitFor(() => expect(putBody).toBeDefined());
  expect(putBody?.events).toEqual([
    {
      label: "Fête des bâtisseurs",
      lane: "lab",
      start: Date.parse("2027-01-10T08:00") / 1000,
      end: Date.parse("2027-01-17T08:00") / 1000,
      duration_pct: 80,
      cost_pct: 50,
    },
  ]);
});

it("refuses an event that ends before it starts and sends nothing", async () => {
  let putCount = 0;
  vi.stubGlobal("fetch", async (input: Request | string) => {
    const request = input instanceof Request ? input : new Request(new URL(input, "http://x"));
    if (request.method === "PUT") putCount += 1;
    return Response.json({ ...SAVED, events: [] });
  });
  const user = userEvent.setup();
  render(<ManualForm villageId="P2Y8QQ0" />);
  await screen.findByText("Valeurs enregistrées");
  await user.click(screen.getByRole("button", { name: "Ajouter un événement" }));
  await user.type(screen.getByLabelText("Nom de l'événement"), "À l'envers");
  await user.type(screen.getByLabelText("Début"), "2027-01-17T08:00");
  await user.type(screen.getByLabelText("Fin"), "2027-01-10T08:00");
  await user.click(screen.getByRole("button", { name: "Enregistrer la saisie" }));
  expect(
    await screen.findByText(
      "Chaque événement a besoin d'un nom, d'un début et d'une fin après le début.",
    ),
  ).toBeDefined();
  expect(putCount).toBe(0);
});
