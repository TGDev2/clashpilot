import { Link } from "react-router";
import type { AssignmentOut } from "../../api/client";
import { fr } from "../../i18n/fr";
import { formatInZone } from "../../lib/format";
import { compareAssignments, laneLabel, sheetHref, statusLabel } from "./lanes";

const cell = "border-b border-line/60 px-3 py-2 align-top";

/** The diagram's content as a table, for screen readers and for reading exact dates. */
export function GanttTable({
  assignments,
  timezone,
}: {
  assignments: readonly AssignmentOut[];
  timezone: string;
}) {
  const sorted = [...assignments].sort(compareAssignments);
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[36rem] border-collapse text-left text-sm">
        <caption className="mb-2 text-left font-display text-lg text-bone">
          {fr.gantt.table}
        </caption>
        <thead>
          <tr className="text-xs tracking-wide text-mist uppercase">
            <th scope="col" className={cell}>
              {fr.gantt.lane}
            </th>
            <th scope="col" className={cell}>
              {fr.gantt.name}
            </th>
            <th scope="col" className={cell}>
              {fr.gantt.start}
            </th>
            <th scope="col" className={cell}>
              {fr.gantt.end}
            </th>
            <th scope="col" className={cell}>
              {fr.gantt.status}
            </th>
          </tr>
        </thead>
        <tbody>
          {sorted.map((a) => (
            <tr key={a.task_id}>
              <td className={cell}>{laneLabel(a.lane, a.lane_slot)}</td>
              <td className={cell}>
                <Link
                  to={sheetHref(a.task_id)}
                  className="text-signal underline underline-offset-2"
                >
                  {a.name}
                </Link>
              </td>
              <td className={`${cell} font-mono`}>{formatInZone(a.start, timezone)}</td>
              <td className={`${cell} font-mono`}>{formatInZone(a.end, timezone)}</td>
              <td className={cell}>{statusLabel(a.status)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
