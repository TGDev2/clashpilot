import { useState } from "react";
import { Link } from "react-aria-components";
import { type ApiProblem, api, settle } from "../api/client";
import { useApi } from "../api/useApi";
import { TextInput } from "../components/forms";
import { ProblemNotice } from "../components/ProblemNotice";
import { Button, PageTitle, Section, TextAlert } from "../components/ui";
import { fr } from "../i18n/fr";
import { useVillage } from "../state/village";

const CUSTOM_GOAL = "custom_goal";

/** Unit ids separated by commas or spaces; `undefined` when a token is not a whole number. */
function parseComposition(text: string): number[] | undefined {
  const tokens = text.split(/[\s,;]+/).filter((token) => token !== "");
  const ids = tokens.map((token) => (/^\d+$/.test(token) ? Number(token) : Number.NaN));
  return ids.every(Number.isSafeInteger) ? ids : undefined;
}

function positiveInteger(text: string): number | undefined {
  return /^\d+$/.test(text.trim()) && Number(text) >= 1 ? Number(text) : undefined;
}

/** Choose a progression strategy, a composition and, for `custom_goal`, a goal; compute the plan. */
export function Strategy() {
  const { villageId } = useVillage();
  const strategies = useApi(() => settle(() => api().GET("/api/meta/strategies")), "strategies");
  const [chosen, setChosen] = useState<string | undefined>();
  const [composition, setComposition] = useState("");
  const [goalId, setGoalId] = useState("");
  const [goalLevel, setGoalLevel] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [localError, setLocalError] = useState<string | undefined>();
  const [problem, setProblem] = useState<ApiProblem | undefined>();

  async function compute() {
    if (villageId === null) {
      return;
    }
    setDone(false);
    setProblem(undefined);
    setLocalError(undefined);
    const body: {
      strategy?: string;
      composition?: number[];
      goal?: { data_id: number; level: number };
    } = {};
    if (chosen !== undefined) {
      body.strategy = chosen;
    }
    const units = parseComposition(composition);
    if (units === undefined) {
      setLocalError(fr.strategy.invalidComposition);
      return;
    }
    if (units.length > 0) {
      body.composition = units;
    }
    if (chosen === CUSTOM_GOAL) {
      const dataId = positiveInteger(goalId);
      const level = positiveInteger(goalLevel);
      if (dataId === undefined || level === undefined) {
        setLocalError(fr.strategy.invalidGoal);
        return;
      }
      body.goal = { data_id: dataId, level };
    }
    setBusy(true);
    const outcome = await settle(() =>
      api().POST("/api/villages/{village_id}/plans", {
        params: { path: { village_id: villageId } },
        body,
      }),
    );
    setBusy(false);
    if (outcome.ok) {
      setDone(true);
    } else {
      setProblem(outcome.problem);
    }
  }

  return (
    <>
      <PageTitle>{fr.strategy.title}</PageTitle>
      <div className="space-y-6">
        {villageId === null && (
          <p className="text-mist">
            {fr.strategy.needVillage}{" "}
            <Link href="/importer" className="text-signal underline underline-offset-2">
              {fr.dashboard.noVillageLink}
            </Link>
          </p>
        )}
        <Section title={fr.strategy.chooseTitle}>
          <p className="text-sm text-mist">{fr.strategy.chooseHint}</p>
          {strategies.problem !== undefined && <TextAlert>{fr.strategy.loadFailed}</TextAlert>}
          <div role="radiogroup" aria-label={fr.strategy.chooseTitle} className="space-y-1">
            {strategies.data?.strategies.map((strategy) => (
              <label
                key={strategy.key}
                className="flex cursor-pointer items-center gap-3 rounded-md px-2 py-2 text-sm hover:bg-deck-hi"
              >
                <input
                  type="radio"
                  name="strategy"
                  value={strategy.key}
                  checked={chosen === strategy.key}
                  onChange={() => setChosen(strategy.key)}
                  className="accent-signal"
                />
                {strategy.label}
              </label>
            ))}
          </div>
        </Section>
        <Section title={fr.strategy.unitsTitle}>
          <TextInput
            label={fr.strategy.composition}
            hint={fr.strategy.compositionHint}
            value={composition}
            onChange={setComposition}
          />
          {chosen === CUSTOM_GOAL && (
            <div className="grid gap-4 sm:grid-cols-2">
              <TextInput
                label={fr.strategy.goalId}
                type="number"
                min={1}
                value={goalId}
                onChange={setGoalId}
              />
              <TextInput
                label={fr.strategy.goalLevel}
                type="number"
                min={1}
                value={goalLevel}
                onChange={setGoalLevel}
              />
            </div>
          )}
        </Section>
        <div className="space-y-3">
          <Button onPress={() => void compute()} isDisabled={busy || villageId === null}>
            {fr.now.compute}
          </Button>
          {busy && <p className="text-sm text-mist">{fr.now.computing}</p>}
          {done && (
            <p role="status" className="text-sm text-confirmed">
              {fr.now.computed}
            </p>
          )}
          {localError !== undefined && <TextAlert>{localError}</TextAlert>}
          <ProblemNotice problem={problem} />
        </div>
      </div>
    </>
  );
}
