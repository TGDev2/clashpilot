import { Link } from "react-aria-components";
import { Outlet, useLocation } from "react-router";
import { fr } from "../i18n/fr";

const LINKS = [
  { to: "/", label: fr.nav.dashboard },
  { to: "/importer", label: fr.nav.import },
  { to: "/maintenant", label: fr.nav.now },
  { to: "/calendrier", label: fr.nav.calendar },
  { to: "/constructeurs", label: fr.nav.builders },
  { to: "/strategie", label: fr.nav.strategy },
  { to: "/ressources", label: fr.nav.resources },
  { to: "/scenarios", label: fr.nav.scenarios },
  { to: "/objets", label: fr.nav.items },
  { to: "/historique", label: fr.nav.history },
  { to: "/reglages", label: fr.nav.settings },
] as const;

export function Layout() {
  const { pathname } = useLocation();
  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-line bg-deck">
        <div className="mx-auto max-w-5xl px-4 pt-5 sm:px-6">
          <h1 className="font-display text-2xl tracking-tight text-bone">{fr.app.title}</h1>
          <p className="text-sm text-mist">{fr.app.tagline}</p>
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
                    {link.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
      </header>
      <main className="mx-auto w-full max-w-5xl flex-1 px-4 py-6 sm:px-6">
        <Outlet />
      </main>
      <footer className="border-t border-line px-4 py-4 sm:px-6">
        <small className="mx-auto block max-w-5xl text-mist">{fr.footer.fanContentPolicy}</small>
      </footer>
    </div>
  );
}
