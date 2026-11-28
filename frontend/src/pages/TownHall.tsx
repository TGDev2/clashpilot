import { type ReactNode, useState } from "react";
import { type ApiProblem, api, type PlanOut, settle, townHallBody } from "../api/client";
import type { components } from "../api/schema";
import { useScheduleZone } from "../api/useZone";
import { CheckboxInput } from "../components/forms";
import { PlanGate } from "../components/PlanGate";
import { ProblemNotice } from "../components/ProblemNotice";
import { ProvenanceBadge } from "../components/ProvenanceBadge";
import { Button, Section } from "../components/ui";
import { fr } from "../i18n/fr";
import { formatDuration, formatInZone, formatSigned } from "../lib/format";
import { useVillage } from "../state/village";

type Comparison = components["schemas"]["TownHallComparisonOut"];
type Option = components["schemas"]["LaunchOptionOut"];

const LAB = "lab";

/** A value the API did not compute is the Inconnu badge, never a 0. */
function Fact({
  label,
  value,
  format,
}: {
  label: string;
  value: number | null;
  format: (value: number) => string;
}) {
  return (
    <p>
      <span className="text-mist">{label}</span>{" "}
      {value === null ? (
        <ProvenanceBadge status="unknown" />
      ) : (
        <span className="font-mono text-bone">{format(value)}</span>
      )}
    </p>
  );
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="space-y-1">
      <h4 className="text-xs font-medium text-mist">{title}</h4> {children}
    </div>
  );
}

function laneLeft(lane: string, count: number, workS: number): string {
  const duration = formatDuration(workS);
  if (lane === LAB) {
    return fr.townHall.labLeft(count, duration);
  }
  const name = (fr.criticalPath.laneName as Record<string, string>)[lane] ?? lane;
  return fr.townHall.laneLeft(name, count, duration);
}

/** What the option leaves behind when its town hall ends. */
function Figures({ option, timezone }: { option: Option; timezone: string }) {
  const date = (epoch: number) => formatInZone(epoch, timezone);
  const lanes = Object.entries(option.unfinished_by_lane);
  const composition = option.unfinished_composition;
  return (
    <>
      <ProvenanceBadge status="estimated" />
      <Fact label={fr.townHall.start} value={option.th_start} format={date} />
      <Fact label={fr.townHall.finish} value={option.th_finish} format={date} />
      <Fact label={fr.townHall.idle} value={option.idle_builder_s} format={formatDuration} />
      <Group title={fr.townHall.unfinished}>
        {lanes.length === 0 && <p>{fr.townHall.nothingUnfinished}</p>}
        <ul className="space-y-1">
          {lanes.map(([lane, count]) => (
            <li key={lane} data-lane={lane}>
              {laneLeft(lane, count, option.unfinished_work_s_by_lane[lane] ?? 0)}
            </li>
          ))}
        </ul>{" "}
        {composition === null ? (
          <ProvenanceBadge status="unknown" />
        ) : (
          <p>
            {fr.townHall.compositionLeft(composition.count, formatDuration(composition.work_s))}
          </p>
        )}
      </Group>
      {option.heroes.length > 0 && (
        <Group title={fr.townHall.heroes}>
          <ul className="space-y-1">
            {option.heroes.map((hero) => (
              <li key={hero.data_id} data-hero={hero.data_id}>
                {fr.townHall.hero(hero.name, hero.level_reached)}
              </li>
            ))}
          </ul>
        </Group>
      )}
    </>
  );
}

function OptionCard({
  option,
  reference,
  timezone,
}: {
  option: Option;
  /** The unfinished count of `after`, null on that card and when `after` has none. */
  reference: number | null;
  timezone: string;
}) {
  return (
    <li
      data-option={option.kind}
      className="space-y-3 rounded-lg border border-line bg-ink/40 p-3 text-sm"
    >
      <h3 className="font-display text-lg text-bone">{fr.townHall.kinds[option.kind]}</h3>{" "}
      {option.status === "ok" ? (
        <>
          <Figures option={option} timezone={timezone} />
          {reference !== null && option.unfinished_count !== null && (
            <p className="border-t border-line/60 pt-2 font-medium text-bone">
              {fr.townHall.gap(formatSigned(option.unfinished_count - reference))}
            </p>
          )}
        </>
      ) : (
        <div className="space-y-1">
          <p className="font-medium text-unknown">{fr.scenarios.status.infeasible}</p>{" "}
          <ul className="list-disc space-y-1 pl-5 text-mist">
            {option.reasons.map((reason) => (
              <li key={reason.message}>{reason.message}</li>
            ))}
          </ul>
        </div>
      )}
    </li>
  );
}

/** The API options side by side, stacked on a phone. */
function Options({ comparison, timezone }: { comparison: Comparison; timezone: string }) {
  const { target, options, scope } = comparison;
  if (target === null) {
    return <p className="text-mist">{fr.criticalPath.noTarget}</p>;
  }
  const after = options.find((option) => option.kind === "after");
  return (
    <Section title={fr.townHall.result(target.to_level)}>
      <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {options.map((option) => (
          <OptionCard
            key={option.kind}
            option={option}
            reference={option.kind === "after" ? null : (after?.unfinished_count ?? null)}
            timezone={timezone}
          />
        ))}
      </ul>
      <p className="text-xs text-mist">{scope}</p>
    </Section>
  );
}

function Comparator({ villageId, plan }: { villageId: string; plan: PlanOut }) {
  const timezone = useScheduleZone(villageId);
  const [skipLab, setSkipLab] = useState((plan.town_hall_skip_lanes ?? []).includes(LAB));
  const [comparison, setComparison] = useState<Comparison | undefined>();
  const [problem, setProblem] = useState<ApiProblem | undefined>();
  const [busy, setBusy] = useState(false);

  function toggle(checked: boolean) {
    setSkipLab(checked);
    // The options shown were computed for the other choice.
    setComparison(undefined);
  }

  async function compare() {
    setBusy(true);
    setComparison(undefined);
    setProblem(undefined);
    // The box only speaks for the lab: the other lanes the plan skips stay skipped.
    const others = (plan.town_hall_skip_lanes ?? []).filter((lane) => lane !== LAB);
    const outcome = await settle(() =>
      api().POST("/api/villages/{village_id}/town-hall/compare", {
        params: { path: { village_id: villageId } },
        body: townHallBody({ skip_lanes: skipLab ? [...others, LAB] : others }),
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
      <p className="text-sm text-mist">{fr.townHall.intro}</p>
      <p className="text-xs text-mist">{fr.now.timeIn(timezone)}</p>
      <div className="space-y-3">
        <CheckboxInput
          label={fr.strategy.skipLab}
          checked={skipLab}
          onChange={toggle}
          disabled={busy}
        />
        <Button onPress={() => void compare()} isDisabled={busy}>
          {fr.townHall.compare}
        </Button>
        {busy && <p className="text-sm text-mist">{fr.townHall.comparing}</p>}
        <ProblemNotice problem={problem} />
      </div>
      {comparison !== undefined && <Options comparison={comparison} timezone={timezone} />}
    </>
  );
}

/** When to launch the next town hall: the API's options side by side, none recommended. */
export function TownHall() {
  const { villageId } = useVillage();
  return (
    <PlanGate title={fr.townHall.title} zoneNote={false}>
      {(plan) => (
        <Comparator key={`${villageId}:${plan.plan_id}`} villageId={villageId ?? ""} plan={plan} />
      )}
    </PlanGate>
  );
}
