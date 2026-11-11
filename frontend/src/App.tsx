import { type ReactNode, useEffect } from "react";
import { RouterProvider } from "react-aria-components";
import { BrowserRouter, Route, Routes, useHref, useNavigate } from "react-router";
import { Layout } from "./components/Layout";
import { fr } from "./i18n/fr";
import { Dashboard } from "./pages/Dashboard";
import { Import } from "./pages/Import";
import { Placeholder } from "./pages/Placeholder";
import { VillageProvider } from "./state/village";

const PLACEHOLDERS = [
  { path: "maintenant", title: fr.nav.now },
  { path: "calendrier", title: fr.nav.calendar },
  { path: "constructeurs", title: fr.nav.builders },
  { path: "scenarios", title: fr.nav.scenarios },
  { path: "historique", title: fr.nav.history },
  { path: "reglages", title: fr.nav.settings },
] as const;

/** Lets react-aria links navigate through the router instead of reloading the page. */
function AriaRouter({ children }: { children: ReactNode }) {
  const navigate = useNavigate();
  return (
    <RouterProvider navigate={navigate} useHref={useHref}>
      {children}
    </RouterProvider>
  );
}

export function App() {
  useEffect(() => {
    document.documentElement.lang = "fr";
  }, []);
  return (
    <BrowserRouter>
      <AriaRouter>
        <VillageProvider>
          <Routes>
            <Route element={<Layout />}>
              <Route index element={<Dashboard />} />
              <Route path="importer" element={<Import />} />
              {PLACEHOLDERS.map((page) => (
                <Route
                  key={page.path}
                  path={page.path}
                  element={<Placeholder title={page.title} />}
                />
              ))}
              <Route path="*" element={<Placeholder title={fr.nav.dashboard} />} />
            </Route>
          </Routes>
        </VillageProvider>
      </AriaRouter>
    </BrowserRouter>
  );
}
