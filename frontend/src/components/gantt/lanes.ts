import type { AssignmentOut } from "../../api/client";
import { fr } from "../../i18n/fr";

const LANE_ORDER = ["builder", "lab", "pet_house", "blacksmith"];

export type LaneRow = { key: string; lane: string; slot: number; label: string };

function laneRank(lane: string): number {
  const rank = LANE_ORDER.indexOf(lane);
  return rank === -1 ? LANE_ORDER.length : rank;
}

/** "Constructeur 1" for builder slot 0: the French lane name and the slot counted from 1. */
export function laneLabel(lane: string, slot: number): string {
  const name = (fr.lanes as Record<string, string>)[lane] ?? lane;
  return `${name} ${slot + 1}`;
}

export function statusLabel(status: AssignmentOut["status"]): string {
  return fr.plan.status[status];
}

/** The order of the diagram and the table: lane (builders first), slot, then start. */
export function compareAssignments(a: AssignmentOut, b: AssignmentOut): number {
  return (
    laneRank(a.lane) - laneRank(b.lane) ||
    a.lane.localeCompare(b.lane) ||
    a.lane_slot - b.lane_slot ||
    a.start - b.start ||
    a.task_id.localeCompare(b.task_id)
  );
}

/** One row per `(lane, lane_slot)` pair of the assignments, in display order. */
export function laneRows(assignments: readonly AssignmentOut[]): LaneRow[] {
  const rows = new Map<string, LaneRow>();
  for (const a of [...assignments].sort(compareAssignments)) {
    const key = `${a.lane}:${a.lane_slot}`;
    if (!rows.has(key)) {
      rows.set(key, {
        key,
        lane: a.lane,
        slot: a.lane_slot,
        label: laneLabel(a.lane, a.lane_slot),
      });
    }
  }
  return [...rows.values()];
}

/** The route of a task's sheet. */
export function sheetHref(taskId: string): string {
  return `/taches/${encodeURIComponent(taskId)}`;
}
