import { api, type PlanOut, type SchedulePreview, settle } from "./client";
import { type ApiResource, useApi } from "./useApi";

/** The latest plan of the village (404 `no_plan` when none was computed). */
export function useLatestPlan(villageId: string | null): ApiResource<PlanOut> {
  return useApi<PlanOut>(
    () =>
      settle(() =>
        api().GET("/api/villages/{village_id}/plans/latest", {
          params: { path: { village_id: villageId ?? "" } },
        }),
      ),
    villageId,
  );
}

/** The UTC windows of the stored schedule over the next `days` days (404 without a schedule). */
export function useSchedulePreview(
  villageId: string | null,
  days: number,
): ApiResource<SchedulePreview> {
  return useApi<SchedulePreview>(
    () =>
      settle(() =>
        api().GET("/api/villages/{village_id}/schedule/preview", {
          params: { path: { village_id: villageId ?? "" }, query: { days } },
        }),
      ),
    villageId === null ? null : `${villageId}:${days}`,
  );
}
