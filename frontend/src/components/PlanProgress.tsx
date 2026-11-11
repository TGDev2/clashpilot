import type { PlanOut } from "../api/client";
import { fr } from "../i18n/fr";
import { formatInZone } from "../lib/format";
import { safeZone } from "../lib/time";
import { ProvenanceBadge } from "./ProvenanceBadge";

function Indicator({ label, value }: { label: string; value: string | undefined }) {
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line/60 py-2 last:border-b-0">
      <span className="w-60 text-bone">{label}</span>{" "}
      {value !== undefined && <span className="font-mono text-bone tabular-nums">{value}</span>}{" "}
      <ProvenanceBadge status={value === undefined ? "unknown" : "estimated"} />
    </li>
  );
}

/**
 * Progress read from the plan answer: assignments whose end has passed, the others, and the latest
 * end. All three are estimates, the plan being one. `nowMs` is the clock of the page.
 */
export function PlanProgress({ plan, nowMs }: { plan: PlanOut; nowMs: number }) {
  const finished = plan.assignments.filter((a) => a.end * 1000 <= nowMs).length;
  const last = plan.assignments.reduce<number | undefined>(
    (latest, a) => (latest === undefined || a.end > latest ? a.end : latest),
    undefined,
  );
  return (
    <ul>
      <Indicator label={fr.dashboard.finishedTasks} value={String(finished)} />
      <Indicator
        label={fr.dashboard.remainingTasks}
        value={String(plan.assignments.length - finished)}
      />
      <Indicator
        label={fr.dashboard.estimatedEnd}
        value={last === undefined ? undefined : formatInZone(last, safeZone(plan.timezone))}
      />
    </ul>
  );
}
