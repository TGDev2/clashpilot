import { Fragment, type ReactNode } from "react";
import { fr } from "../i18n/fr";
import { useVillage } from "../state/village";
import { PageTitle } from "./ui";

/**
 * A page of the chosen base. Switching base starts it afresh: nothing read, typed or computed for
 * the other one stays on screen or goes into a request.
 */
export function PerBase({ children }: { children: ReactNode }) {
  const { base } = useVillage();
  return <Fragment key={base}>{children}</Fragment>;
}

/**
 * A page the API only serves for the home village: on the builder base it says so, and the page is
 * not mounted, so nothing is asked from the server.
 */
export function HomeOnly({ title, children }: { title: string; children: ReactNode }) {
  const { base } = useVillage();
  if (base === "home") {
    return children;
  }
  return (
    <>
      <PageTitle>{title}</PageTitle>
      <p className="text-mist">{fr.village.homeOnly}</p>
    </>
  );
}
