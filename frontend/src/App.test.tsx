import { render, screen } from "@testing-library/react";
import { App } from "./App";
import { fr } from "./i18n/fr";

describe("App", () => {
  it("shows the ClashPilot title", () => {
    render(<App />);
    expect(screen.getByRole("heading", { name: "ClashPilot" })).toBeTruthy();
  });

  it("shows the Fan Content Policy notice in the footer", () => {
    render(<App />);
    expect(screen.getByText(fr.footer.fanContentPolicy)).toBeTruthy();
  });
});
