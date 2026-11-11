import { type ReactNode, useEffect } from "react";
import { RouterProvider } from "react-aria-components";
import { BrowserRouter, Route, Routes, useHref, useNavigate } from "react-router";
import { Layout } from "./components/Layout";
import { fr } from "./i18n/fr";
import { Calendar } from "./pages/Calendar";
import { Dashboard } from "./pages/Dashboard";
import { Gantt } from "./pages/Gantt";
import { History } from "./pages/History";
import { Import } from "./pages/Import";
import { LevelSheet } from "./pages/LevelSheet";
import { MagicItems } from "./pages/MagicItems";
import { NowPage } from "./pages/NowPage";
import { Placeholder } from "./pages/Placeholder";
import { Resources } from "./pages/Resources";
import { Scenarios } from "./pages/Scenarios";
import { Settings } from "./pages/Settings";
import { Strategy } from "./pages/Strategy";
import { TaskSheet } from "./pages/TaskSheet";
import { VillageProvider } from "./state/village";

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
              <Route path="maintenant" element={<NowPage />} />
              <Route path="historique" element={<History />} />
              <Route path="reglages" element={<Settings />} />
              <Route path="calendrier" element={<Calendar />} />
              <Route path="constructeurs" element={<Gantt />} />
              <Route path="taches/:taskId" element={<TaskSheet />} />
              <Route path="strategie" element={<Strategy />} />
              <Route path="ressources" element={<Resources />} />
              <Route path="scenarios" element={<Scenarios />} />
              <Route path="objets" element={<MagicItems />} />
              <Route path="donnees/:packId/:dataId/:level" element={<LevelSheet />} />
              <Route path="*" element={<Placeholder title={fr.nav.dashboard} />} />
            </Route>
          </Routes>
        </VillageProvider>
      </AriaRouter>
    </BrowserRouter>
  );
}
