import type { ReactNode } from "react";
import { Link } from "react-router";
import type { PlanOut } from "../api/client";
import { useLatestPlan } from "../api/usePlan";
import { fr } from "../i18n/fr";
import { useVillage } from "../state/village";
import { Banner, PageTitle, ProblemAlert } from "./ui";

const linkClass = "text-signal underline underline-offset-2";

/** Said when there is no plan to show, with the way to compute one. */
export function NoPlan({ hasVillage }: { hasVillage: boolean }) {
  return (
    <div className="space-y-2 text-mist">
      <p>
        {fr.plan.none}{" "}
        <Link to="/maintenant" className={linkClass}>
          {fr.nav.now}
        </Link>
        .
      </p>
      {!hasVillage && (
        <p>
          {fr.plan.noVillage}{" "}
          <Link to="/importer" className={linkClass}>
            {fr.dashboard.noVillageLink}
          </Link>
        </p>
      )}
    </div>
  );
}

/**
 * A page reading the latest plan of the current village: its title, then the loading state,
 * "Aucun plan" or the API problem, and the page body once the plan is there.
 */
export function PlanGate({
  title,
  children,
}: {
  title: string;
  children: (plan: PlanOut) => ReactNode;
}) {
  const { villageId } = useVillage();
  const latest = useLatestPlan(villageId);
  const code = latest.problem?.code;
  const noPlan = villageId === null || code === "no_plan" || code === "village_not_found";
  const plan = latest.data;
  return (
    <>
      <PageTitle>{title}</PageTitle>
      <div className="space-y-6">
        {latest.loading && plan === undefined && <p className="text-mist">{fr.common.loading}</p>}
        {noPlan ? (
          <NoPlan hasVillage={villageId !== null} />
        ) : (
          <ProblemAlert problem={latest.problem} />
        )}
        {plan !== undefined && !noPlan && (
          <>
            {plan.pack.fictional && <Banner>{fr.dashboard.fictionalBanner}</Banner>}
            <p className="text-xs text-mist">{fr.now.timeIn(plan.timezone)}</p>
            {children(plan)}
          </>
        )}
      </div>
    </>
  );
}
