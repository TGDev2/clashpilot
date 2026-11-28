import { type ReactNode, useEffect } from "react";
import { RouterProvider } from "react-aria-components";
import { BrowserRouter, Route, Routes, useHref, useNavigate } from "react-router";
import { Layout } from "./components/Layout";
import { HomeOnly, PerBase } from "./components/VillagePages";
import { fr } from "./i18n/fr";
import { Calendar } from "./pages/Calendar";
import { Dashboard } from "./pages/Dashboard";
import { Gantt } from "./pages/Gantt";
import { History } from "./pages/History";
import { Import } from "./pages/Import";
import { ItemLevelsPage, LevelSheet } from "./pages/LevelSheet";
import { MagicItems } from "./pages/MagicItems";
import { NowPage } from "./pages/NowPage";
import { Placeholder } from "./pages/Placeholder";
import { Resources } from "./pages/Resources";
import { Scenarios } from "./pages/Scenarios";
import { Settings } from "./pages/Settings";
import { Strategy } from "./pages/Strategy";
import { TaskSheet } from "./pages/TaskSheet";
import { TownHall } from "./pages/TownHall";
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
              <Route
                index
                element={
                  <PerBase>
                    <Dashboard />
                  </PerBase>
                }
              />
              <Route path="importer" element={<Import />} />
              <Route
                path="maintenant"
                element={
                  <PerBase>
                    <NowPage />
                  </PerBase>
                }
              />
              <Route
                path="historique"
                element={
                  <PerBase>
                    <History />
                  </PerBase>
                }
              />
              <Route path="reglages" element={<Settings />} />
              <Route
                path="calendrier"
                element={
                  <PerBase>
                    <Calendar />
                  </PerBase>
                }
              />
              <Route
                path="constructeurs"
                element={
                  <PerBase>
                    <Gantt />
                  </PerBase>
                }
              />
              <Route
                path="taches/:taskId"
                element={
                  <PerBase>
                    <TaskSheet />
                  </PerBase>
                }
              />
              <Route
                path="strategie"
                element={
                  <PerBase>
                    <Strategy />
                  </PerBase>
                }
              />
              <Route
                path="hdv"
                element={
                  <PerBase>
                    <TownHall />
                  </PerBase>
                }
              />
              <Route
                path="ressources"
                element={
                  <HomeOnly title={fr.forecast.title}>
                    <Resources />
                  </HomeOnly>
                }
              />
              <Route
                path="scenarios"
                element={
                  <HomeOnly title={fr.scenarios.title}>
                    <Scenarios />
                  </HomeOnly>
                }
              />
              <Route
                path="objets"
                element={
                  <HomeOnly title={fr.magic.title}>
                    <MagicItems />
                  </HomeOnly>
                }
              />
              <Route path="donnees/:packId/:dataId" element={<ItemLevelsPage />} />
              <Route path="donnees/:packId/:dataId/:level" element={<LevelSheet />} />
              <Route path="*" element={<Placeholder title={fr.nav.dashboard} />} />
            </Route>
          </Routes>
        </VillageProvider>
      </AriaRouter>
    </BrowserRouter>
  );
}
