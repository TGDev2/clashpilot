import { useState } from "react";
import { Link } from "react-aria-components";
import {
  type ActionOut,
  type ApiProblem,
  api,
  type PlanOut,
  type RecommendationsOut,
  settle,
} from "../api/client";
import { useApi } from "../api/useApi";
import { ProofLabel } from "../components/ProofLabel";
import { Banner, Button, PageTitle, ProblemAlert, Section } from "../components/ui";
import { fr } from "../i18n/fr";
import { formatInZone } from "../lib/format";
import { uniqueKeys } from "../lib/keys";
import { useVillage } from "../state/village";

type Explained = ActionOut["advantages"][number];

function Messages({ title, items }: { title?: string; items: Explained[] }) {
  if (items.length === 0) {
    return null;
  }
  const keys = uniqueKeys(items.map((item) => item.code));
  return (
    <div>
      {title !== undefined && (
        <h4 className="text-xs font-medium tracking-wide text-mist uppercase">{title}</h4>
      )}
      <ul className="mt-1 list-disc space-y-1 pl-5 text-sm">
        {items.map((item, index) => (
          <li key={keys[index]}>{item.message}</li>
        ))}
      </ul>
    </div>
  );
}

function ActionCard({ action, timezone }: { action: ActionOut; timezone: string }) {
  const waiting = action.action === "wait";
  const level = action.reliability.level;
  return (
    <article className="space-y-3 rounded-lg border border-line/70 bg-ink p-4">
      <header className="space-y-1">
        <h3 className="font-display text-lg text-bone">{action.name}</h3>
        <p className="font-mono text-sm text-mist">
          {waiting ? fr.now.wait : fr.now.start} {formatInZone(action.at, timezone)}
          {action.end !== null && action.end !== undefined && (
            <>
              {" "}
              ({fr.now.until} {formatInZone(action.end, timezone)})
            </>
          )}{" "}
        </p>
      </header>{" "}
      <Messages title={fr.now.why} items={action.why.reasons} />{" "}
      <Messages title={fr.now.advantages} items={action.advantages} />{" "}
      <Messages title={fr.now.drawbacks} items={action.drawbacks} />{" "}
      <div>
        <h4 className="text-xs font-medium tracking-wide text-mist uppercase">
          {fr.now.reliability}
        </h4>
        <p className="mt-1 text-sm font-medium text-bone">{fr.now.level[level]}</p>
        <Messages items={action.reliability.factors} />
      </div>
    </article>
  );
}

function MissingInputAlert({ problem }: { problem: ApiProblem }) {
  const needsSchedule = problem.missing.includes("schedule_profile");
  const needsManual = problem.missing.some((name) => name !== "schedule_profile");
  return (
    <div role="alert" className="rounded-lg border border-unknown/60 bg-unknown/10 p-3 text-sm">
      <p>{fr.now.missingIntro}</p>
      <ul className="mt-2 list-disc space-y-1 pl-5 font-mono text-xs">
        {problem.missing.map((name) => (
          <li key={name}>{name}</li>
        ))}
      </ul>
      <p className="mt-2 flex flex-wrap gap-x-4">
        {needsManual && (
          <Link href="/importer#saisie" className="text-signal underline underline-offset-2">
            {fr.now.missingManual}
          </Link>
        )}
        {needsSchedule && (
          <Link href="/reglages" className="text-signal underline underline-offset-2">
            {fr.now.missingSchedule}
          </Link>
        )}
      </p>
    </div>
  );
}

/** The next actions of the latest plan, and the button that computes a new plan. */
export function NowPage() {
  const { villageId } = useVillage();
  const reco = useApi<RecommendationsOut>(
    () =>
      settle(() =>
        api().GET("/api/villages/{village_id}/recommendations/now", {
          params: { path: { village_id: villageId ?? "" } },
        }),
      ),
    villageId,
  );
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [lastPlan, setLastPlan] = useState<PlanOut | undefined>();
  const [problem, setProblem] = useState<ApiProblem | undefined>();

  async function compute() {
    if (villageId === null) {
      return;
    }
    setBusy(true);
    setDone(false);
    setProblem(undefined);
    // No engine and no profile: the server's defaults apply (auto when it exists, profile fast).
    const outcome = await settle(() =>
      api().POST("/api/villages/{village_id}/plans", {
        params: { path: { village_id: villageId } },
        body: {},
      }),
    );
    setBusy(false);
    if (outcome.ok) {
      setLastPlan(outcome.data);
      setDone(true);
      reco.reload();
    } else {
      setProblem(outcome.problem);
    }
  }

  const answer = reco.data;
  const proof = lastPlan?.proof ?? answer?.proof;
  const fictional = answer?.fictional === true || lastPlan?.pack.fictional === true;
  const noPlan = reco.problem?.code === "no_plan";
  const loadProblem = noPlan ? undefined : reco.problem;
  const actionKeys = uniqueKeys(
    (answer?.actions ?? []).map((a) => `${a.task_id ?? "wait"}-${a.at}`),
  );
  const announcement = busy ? fr.now.computing : done ? fr.now.computed : "";

  return (
    <>
      <PageTitle>{fr.now.title}</PageTitle>
      <div className="space-y-6">
        {fictional && <Banner>{fr.dashboard.fictionalBanner}</Banner>}
        {villageId === null && (
          <p className="text-mist">
            {fr.now.needVillage}{" "}
            <Link href="/importer" className="text-signal underline underline-offset-2">
              {fr.dashboard.noVillageLink}
            </Link>
          </p>
        )}
        <div className="flex flex-wrap items-center gap-4">
          <Button isDisabled={villageId === null || busy} onPress={() => void compute()}>
            {fr.now.compute}
          </Button>
          <p aria-live="polite" className="text-sm text-mist">
            {announcement}
          </p>
        </div>
        {problem?.code === "missing_input" ? (
          <MissingInputAlert problem={problem} />
        ) : (
          <ProblemAlert problem={problem} />
        )}
        <ProblemAlert problem={loadProblem} />
        {proof !== undefined && (
          <p className="text-sm">
            <ProofLabel proof={proof} />
            {lastPlan?.reproducible === false && (
              <>
                {" "}
                <span className="text-mist">{fr.proof.fastResult}</span>
              </>
            )}
          </p>
        )}
        {noPlan && lastPlan === undefined && <p className="text-mist">{fr.now.noPlan}</p>}
        {answer !== undefined && (
          <Section title={fr.now.actions}>
            <p className="text-xs text-mist">{fr.now.timeIn(answer.timezone)}</p>
            {answer.actions.length === 0 ? (
              <p className="text-mist">{fr.now.noActions}</p>
            ) : (
              <div className="space-y-4">
                {answer.actions.map((action, index) => (
                  <ActionCard key={actionKeys[index]} action={action} timezone={answer.timezone} />
                ))}
              </div>
            )}
          </Section>
        )}
      </div>
    </>
  );
}
