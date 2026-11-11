import { useId } from "react";
import { Link, useParams } from "react-router";
import type { AssignmentOut, PlanOut } from "../api/client";
import { laneLabel, sheetHref, statusLabel } from "../components/gantt/lanes";
import { PlanGate } from "../components/PlanGate";
import { fr } from "../i18n/fr";
import { formatInZone } from "../lib/format";
import { safeZone } from "../lib/time";

const linkClass = "text-signal underline underline-offset-2";

/** A list of tasks, each a link to its own sheet; the dependencies of a task drawn as links. */
function TaskLinks({
  title,
  empty,
  ids,
  plan,
}: {
  title: string;
  empty: string;
  ids: readonly string[];
  plan: PlanOut;
}) {
  const headingId = useId();
  const timezone = safeZone(plan.timezone);
  return (
    <section className="space-y-2">
      <h4 id={headingId} className="text-xs font-medium tracking-wide text-mist uppercase">
        {title}
      </h4>
      {ids.length === 0 ? (
        <p className="text-sm text-mist">{empty}</p>
      ) : (
        <ul aria-labelledby={headingId} className="list-disc space-y-1 pl-5 text-sm">
          {ids.map((id) => {
            const task = plan.assignments.find((a) => a.task_id === id);
            return (
              <li key={id}>
                <Link to={sheetHref(id)} className={linkClass}>
                  {task?.name ?? id}
                </Link>
                {task !== undefined && (
                  <>
                    {" "}
                    <span className="text-mist">
                      ({statusLabel(task.status)}, {fr.task.endsAt}{" "}
                      {formatInZone(task.end, timezone)})
                    </span>
                  </>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function TaskView({ plan, taskId }: { plan: PlanOut; taskId: string }) {
  const task = plan.assignments.find((a) => a.task_id === taskId);
  if (task === undefined) {
    return (
      <p className="text-mist">
        {fr.task.notInPlan}{" "}
        <Link to="/constructeurs" className={linkClass}>
          {fr.task.back}
        </Link>
      </p>
    );
  }
  const timezone = safeZone(plan.timezone);
  const unlocks = plan.assignments
    .filter((a: AssignmentOut) => a.requires.includes(task.task_id))
    .map((a) => a.task_id);
  const facts = [
    [fr.task.lane, laneLabel(task.lane, task.lane_slot)],
    [fr.task.status, statusLabel(task.status)],
    [fr.task.start, formatInZone(task.start, timezone)],
    [fr.task.end, formatInZone(task.end, timezone)],
  ] as const;
  return (
    <article className="space-y-6 rounded-xl border border-line bg-deck p-4 sm:p-6">
      <h3 className="font-display text-2xl text-bone">{task.name}</h3>
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
        {facts.map(([label, value]) => (
          <div key={label} className="contents">
            <dt className="text-mist">{label}</dt> <dd className="font-mono text-bone">{value}</dd>
          </div>
        ))}
      </dl>
      <TaskLinks
        title={fr.task.requires}
        empty={fr.task.noRequires}
        ids={task.requires}
        plan={plan}
      />
      <p className="text-sm">
        <Link
          to={`/donnees/${encodeURIComponent(plan.pack.pack_id)}/${task.data_id}`}
          className={linkClass}
        >
          {fr.task.gameData}
        </Link>
      </p>
      <TaskLinks title={fr.task.unlocks} empty={fr.task.noUnlocks} ids={unlocks} plan={plan} />
    </article>
  );
}

/** Fiche d'une tâche: its dates and its dependencies, each linked to its own sheet. */
export function TaskSheet() {
  const { taskId = "" } = useParams();
  return (
    <PlanGate title={fr.task.title}>{(plan) => <TaskView plan={plan} taskId={taskId} />}</PlanGate>
  );
}
