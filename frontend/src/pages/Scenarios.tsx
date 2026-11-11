import { useState } from "react";
import { type ApiProblem, api, settle } from "../api/client";
import type { components } from "../api/schema";
import { useApi } from "../api/useApi";
import { useScheduleZone } from "../api/useZone";
import { ProblemNotice } from "../components/ProblemNotice";
import { Button, PageTitle, Section } from "../components/ui";
import { fr } from "../i18n/fr";
import { useVillage } from "../state/village";
import { CompareTable } from "./scenarios/CompareTable";
import { ScenarioForm } from "./scenarios/ScenarioForm";

type Scenario = components["schemas"]["ScenarioOut"];
type Compare = components["schemas"]["CompareOut"];

function overlaySummary(scenario: Scenario): string {
  const labels = scenario.overlays.map(
    (overlay) => (fr.scenarios.overlays as Record<string, string>)[String(overlay.type)] ?? "",
  );
  return labels.filter((label) => label !== "").join(", ");
}

function Entry({
  scenario,
  checked,
  onToggle,
  onDelete,
}: {
  scenario: Scenario;
  checked: boolean;
  onToggle: () => void;
  onDelete: () => void;
}) {
  const ok = scenario.status === "ok";
  return (
    <li className="space-y-2 border-b border-line/60 py-3 last:border-b-0">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        {ok && (
          <input
            type="checkbox"
            aria-label={fr.scenarios.compareFor(scenario.name)}
            checked={checked}
            onChange={onToggle}
            className="size-4 accent-signal"
          />
        )}
        <span className="font-medium text-bone">{scenario.name}</span>
        <span className="text-xs text-mist">{overlaySummary(scenario)}</span>
        <Button onPress={onDelete} className="ml-auto">
          {fr.scenarios.delete}
        </Button>
      </div>
      {!ok && (
        <div className="space-y-1 text-sm">
          <p className="font-medium text-unknown">
            {scenario.status === "unsupported"
              ? fr.scenarios.status.unsupported
              : fr.scenarios.status.infeasible}
          </p>
          <ul className="list-disc space-y-1 pl-5 text-mist">
            {scenario.reasons.map((reason) => (
              <li key={reason.code}>{reason.message}</li>
            ))}
          </ul>
        </div>
      )}
    </li>
  );
}

/** What-if scenarios: create, list, compare with the real plan side by side, delete. */
export function Scenarios() {
  const { villageId } = useVillage();
  const timezone = useScheduleZone(villageId);
  const list = useApi<Scenario[]>(
    () =>
      settle(() =>
        api().GET("/api/villages/{village_id}/scenarios", {
          params: { path: { village_id: villageId ?? "" } },
        }),
      ),
    villageId,
  );
  const [selected, setSelected] = useState<number[]>([]);
  const [comparison, setComparison] = useState<Compare | undefined>();
  const [problem, setProblem] = useState<ApiProblem | undefined>();
  const [busy, setBusy] = useState(false);
  const scenarios = list.data ?? [];

  function toggle(id: number) {
    setSelected((previous) =>
      previous.includes(id) ? previous.filter((other) => other !== id) : [...previous, id],
    );
  }

  async function remove(id: number) {
    if (villageId === null) {
      return;
    }
    setProblem(undefined);
    // A 204 carries no body, which `settle` reads as a failure: read the response itself.
    const outcome = await settle(async () => {
      const { error, response } = await api().DELETE(
        "/api/villages/{village_id}/scenarios/{scenario_id}",
        { params: { path: { village_id: villageId, scenario_id: id } } },
      );
      return { data: response.ok ? true : undefined, error, response };
    });
    if (outcome.ok || outcome.problem.code === "scenario_not_found") {
      setSelected((previous) => previous.filter((other) => other !== id));
      setComparison(undefined);
      list.reload();
    } else {
      setProblem(outcome.problem);
    }
  }

  async function compare() {
    if (villageId === null) {
      return;
    }
    setBusy(true);
    setProblem(undefined);
    // The order of the list, whatever the order of the clicks.
    const ids = scenarios.map((s) => s.scenario_id).filter((id) => selected.includes(id));
    const outcome = await settle(() =>
      api().POST("/api/villages/{village_id}/scenarios/compare", {
        params: { path: { village_id: villageId } },
        body: { scenario_ids: ids },
      }),
    );
    setBusy(false);
    if (outcome.ok) {
      setComparison(outcome.data);
    } else {
      setProblem(outcome.problem);
    }
  }

  return (
    <>
      <PageTitle>{fr.scenarios.title}</PageTitle>
      <div className="space-y-6">
        <p className="text-sm text-mist">{fr.scenarios.intro}</p>
        <ProblemNotice problem={list.problem} />
        <Section title={fr.scenarios.list}>
          {list.data !== undefined && scenarios.length === 0 && (
            <p className="text-sm text-mist">{fr.scenarios.empty}</p>
          )}
          <ul>
            {scenarios.map((scenario) => (
              <Entry
                key={scenario.scenario_id}
                scenario={scenario}
                checked={selected.includes(scenario.scenario_id)}
                onToggle={() => toggle(scenario.scenario_id)}
                onDelete={() => void remove(scenario.scenario_id)}
              />
            ))}
          </ul>
          <Button onPress={() => void compare()} isDisabled={busy || selected.length === 0}>
            {fr.scenarios.compareSelection}
          </Button>
          <ProblemNotice problem={problem} />
        </Section>
        {comparison !== undefined && <CompareTable comparison={comparison} timezone={timezone} />}
        <ScenarioForm villageId={villageId} onCreated={list.reload} />
      </div>
    </>
  );
}
