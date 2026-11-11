import { Link } from "react-router";
import type { PlanOut } from "../api/client";
import { useSchedulePreview } from "../api/usePlan";
import { GanttChart } from "../components/gantt/GanttChart";
import { GanttTable } from "../components/gantt/GanttTable";
import { PlanGate } from "../components/PlanGate";
import { fr } from "../i18n/fr";
import { SECONDS_PER_DAY, safeZone } from "../lib/time";
import { useVillage } from "../state/village";

/** The preview starts at the server's now: ask for enough days to reach the plan's last end. */
function previewDays(plan: PlanOut): number {
  const lastEnd = Math.max(plan.now, ...plan.assignments.map((a) => a.end));
  const days = Math.ceil((lastEnd - Date.now() / 1000) / SECONDS_PER_DAY) + 1;
  return Math.min(366, Math.max(1, days));
}

function GanttView({ plan }: { plan: PlanOut }) {
  const { villageId } = useVillage();
  const preview = useSchedulePreview(villageId, previewDays(plan));
  const timezone = safeZone(plan.timezone);
  if (plan.assignments.length === 0) {
    return <p className="text-mist">{fr.gantt.empty}</p>;
  }
  return (
    <>
      {preview.problem?.code === "schedule_not_found" && (
        <p className="text-sm text-mist">
          {fr.gantt.noSchedule}{" "}
          <Link to="/reglages" className="text-signal underline underline-offset-2">
            {fr.gantt.setSchedule}
          </Link>
        </p>
      )}
      <GanttChart assignments={plan.assignments} timezone={timezone} preview={preview.data} />
      <GanttTable assignments={plan.assignments} timezone={timezone} />
    </>
  );
}

/** Constructeurs: the latest plan as a Gantt diagram and the equivalent table. */
export function Gantt() {
  return <PlanGate title={fr.gantt.title}>{(plan) => <GanttView plan={plan} />}</PlanGate>;
}
