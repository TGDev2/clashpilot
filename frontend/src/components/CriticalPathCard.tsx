import { api, baseParams, type CriticalPathOut, type EstimateOut, settle } from "../api/client";
import { useApi } from "../api/useApi";
import { useScheduleZone } from "../api/useZone";
import { fr } from "../i18n/fr";
import { formatDuration, formatInZone, laneSubject } from "../lib/format";
import { useLabels, useVillage } from "../state/village";
import { ProblemNotice } from "./ProblemNotice";
import { ProvenanceBadge } from "./ProvenanceBadge";
import { Section, Tag } from "./ui";

type Labels = ReturnType<typeof useLabels>["criticalPath"];

/** The name of a lane, the lane key itself when it has none. */
function laneName(lane: string, labels: Labels): string {
  return (labels.laneName as Record<string, string>)[lane] ?? lane;
}

/** What fixes the date: the longest chains, or the work left on one lane. */
function cause(path: CriticalPathOut, labels: Labels): string | undefined {
  const bound = path.bound;
  if (bound === null) {
    return undefined;
  }
  if (bound.kind === "chain") {
    return path.chains.map((chain) => chain.name).join(", ") || undefined;
  }
  const lane = bound.lane ?? "";
  const subject = laneSubject(lane, labels.laneSubject);
  const work = path.work_finish.find((entry) => entry.lane === lane);
  return work === undefined ? subject : `${subject} (${formatDuration(work.work_s)})`;
}

/** A predicted date in `zone`; an absent one is the Inconnu badge. */
function dateText(estimate: EstimateOut, zone: string) {
  return estimate.value === null ? (
    <ProvenanceBadge status="unknown" />
  ) : (
    formatInZone(estimate.value, zone)
  );
}

function PathDetails({ path, zone }: { path: CriticalPathOut; zone: string }) {
  const t = useLabels();
  if (path.target === null) {
    return <p className="text-mist">{t.criticalPath.noTarget}</p>;
  }
  const fixedBy = cause(path, t.criticalPath);
  return (
    <>
      <div className="space-y-2">
        <p data-date="parallel">
          <span className="block text-sm text-mist">
            {t.criticalPath.earliest(path.target.to_level)}
          </span>{" "}
          <span className="block font-display text-3xl text-bone tabular-nums sm:text-4xl">
            {dateText(path.finish_parallel, zone)}
          </span>
        </p>{" "}
        {path.finish_parallel.value !== null && (
          <ProvenanceBadge status={path.finish_parallel.status} />
        )}{" "}
        <p data-date="after" className="text-sm text-mist">
          {t.criticalPath.after}{" "}
          <span className="font-mono text-bone tabular-nums">
            {dateText(path.finish_after, zone)}
          </span>
        </p>{" "}
        {fixedBy !== undefined && (
          <p className="text-sm font-medium text-bone">{fr.criticalPath.fixedBy(fixedBy)}</p>
        )}{" "}
        {path.calendar_wait_s !== null && path.calendar_wait_s > 0 && (
          <p className="text-sm text-estimated">
            {fr.criticalPath.sleepDelay(formatDuration(path.calendar_wait_s))}
          </p>
        )}
      </div>{" "}
      {path.chains.length > 0 && (
        <div>
          <h3 className="text-xs font-medium tracking-wide text-mist uppercase">
            {fr.criticalPath.chains}
          </h3>{" "}
          <ul className="mt-1 space-y-1 text-sm">
            {path.chains.map((chain) => (
              <li key={chain.data_id} data-chain="">
                {fr.criticalPath.chain(chain.name, chain.from_level, chain.to_level)}{" "}
              </li>
            ))}
          </ul>
        </div>
      )}{" "}
      {path.work_finish.length > 0 && (
        <div>
          <h3 className="text-xs font-medium tracking-wide text-mist uppercase">
            {fr.criticalPath.lanes}
          </h3>{" "}
          <ul className="mt-1">
            {path.work_finish.map((entry) => (
              <li
                key={entry.lane}
                data-lane-work={entry.lane}
                className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line/60 py-2 text-sm last:border-b-0"
              >
                <span className="text-bone">{laneName(entry.lane, t.criticalPath)}</span>{" "}
                <span className="font-mono text-mist tabular-nums">
                  {entry.finish.value === null ? (
                    <ProvenanceBadge status="unknown" />
                  ) : (
                    fr.criticalPath.laneEnd(formatInZone(entry.finish.value, zone))
                  )}
                </span>{" "}
                {path.critical_lanes.includes(entry.lane) && (
                  <Tag tone="critical">{fr.criticalPath.neverStop}</Tag>
                )}{" "}
              </li>
            ))}
          </ul>
        </div>
      )}{" "}
      <p className="text-xs text-mist">{fr.now.timeIn(zone)}</p>
    </>
  );
}

/**
 * The earliest date of the next town hall and what fixes it, as the API computes them. It reads
 * the route once `ready`, with the lanes the latest plan does not wait for.
 */
export function CriticalPathCard({
  villageId,
  skipLanes,
  ready,
}: {
  villageId: string;
  skipLanes: readonly string[];
  ready: boolean;
}) {
  const { base } = useVillage();
  const t = useLabels();
  const zone = useScheduleZone(villageId);
  const path = useApi<CriticalPathOut>(
    () =>
      settle(() =>
        api().GET("/api/villages/{village_id}/critical-path", {
          params: {
            path: { village_id: villageId },
            query: { skip_lanes: [...skipLanes], ...baseParams(base) },
          },
        }),
      ),
    ready ? `${villageId}:${base}:${skipLanes.join(",")}` : null,
  );
  return (
    <div data-critical-path="">
      <Section title={t.criticalPath.title}>
        {/* It qualifies every date below, so it is read first. */}
        {skipLanes.includes("lab") && (
          <p className="text-sm text-signal">{t.criticalPath.labSkipped}</p>
        )}{" "}
        <ProblemNotice problem={path.problem} />
        {path.data !== undefined ? (
          <PathDetails path={path.data} zone={zone} />
        ) : (
          path.problem === undefined && <p className="text-mist">{fr.criticalPath.loading}</p>
        )}
      </Section>
    </div>
  );
}
