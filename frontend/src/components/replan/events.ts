import { api, type Outcome, type ReplanOut, settle } from "../../api/client";

/** The two events the interface records; the server replans and answers what changed. */
export type ReplanEvent =
  | { type: "task_started"; task_id: string; at: number }
  | { type: "snapshot_imported" };

/** `POST .../plans/{plan_id}/events`: a dry run only reports, the other call records the event. */
export function sendEvent(
  villageId: string,
  planId: number,
  event: ReplanEvent,
  dryRun: boolean,
): Promise<Outcome<ReplanOut>> {
  return settle(() =>
    api().POST("/api/villages/{village_id}/plans/{plan_id}/events", {
      params: { path: { village_id: villageId, plan_id: planId } },
      body: { ...event, dry_run: dryRun },
    }),
  );
}
