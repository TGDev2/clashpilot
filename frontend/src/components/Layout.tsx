import { Link } from "react-aria-components";
import { Outlet, useLocation } from "react-router";
import type { Base } from "../api/client";
import { fr } from "../i18n/fr";
import { useLabels, useVillage } from "../state/village";
import { ReminderAnnouncer } from "./ReminderAnnouncer";
import { inputClass } from "./ui";

const LINKS = [
  { to: "/", key: "dashboard" },
  { to: "/importer", key: "import" },
  { to: "/maintenant", key: "now" },
  { to: "/calendrier", key: "calendar" },
  { to: "/constructeurs", key: "builders" },
  { to: "/strategie", key: "strategy" },
  { to: "/hdv", key: "townHall" },
  { to: "/ressources", key: "resources" },
  { to: "/scenarios", key: "scenarios" },
  { to: "/objets", key: "items" },
  { to: "/historique", key: "history" },
  { to: "/reglages", key: "settings" },
] as const;

/** Which base of the village every page shows. */
function BaseSelector() {
  const { base, setBase } = useVillage();
  return (
    <label className="block w-full space-y-1 sm:w-auto">
      <span className="block text-xs text-mist">{fr.village.selector}</span>
      <select
        value={base}
        onChange={(event) => setBase(event.target.value as Base)}
        className={`${inputClass} sm:w-56`}
      >
        <option value="home">{fr.village.home}</option>
        <option value="builder">{fr.village.builder}</option>
      </select>
    </label>
  );
}

export function Layout() {
  const { pathname } = useLocation();
  const { nav } = useLabels();
  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-line bg-deck">
        <div className="mx-auto flex max-w-5xl flex-wrap items-end justify-between gap-3 px-4 pt-5 sm:px-6">
          <div>
            <h1 className="font-display text-2xl tracking-tight text-bone">{fr.app.title}</h1>
            <p className="text-sm text-mist">{fr.app.tagline}</p>
          </div>
          <BaseSelector />
        </div>
        <nav
          aria-label={fr.app.mainNav}
          className="mx-auto mt-4 max-w-5xl overflow-x-auto px-2 sm:px-4"
        >
          <ul className="flex min-w-max gap-1 pb-2">
            {LINKS.map((link) => {
              const current = pathname === link.to;
              return (
                <li key={link.to}>
                  <Link
                    href={link.to}
                    aria-current={current ? "page" : undefined}
                    className={`block rounded-md border-b-2 px-3 py-2 text-sm whitespace-nowrap transition-colors motion-reduce:transition-none ${
                      current
                        ? "border-signal text-bone"
                        : "border-transparent text-mist hover:bg-deck-hi hover:text-bone"
                    }`}
                  >
                    {nav[link.key]}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      </header>
      <ReminderAnnouncer />
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-6 sm:px-6">
        <Outlet />
      </main>
      <footer className="border-t border-line px-4 py-4 sm:px-6">
        <small className="mx-auto block max-w-5xl text-mist">{fr.footer.fanContentPolicy}</small>
      </footer>
    </div>
  );
}
