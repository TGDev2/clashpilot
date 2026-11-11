import { safeZone } from "../lib/time";
import { api, settle } from "./client";
import { useApi } from "./useApi";

/** The zone of the village's schedule, as the API sends it; UTC until it is known or without one. */
export function useScheduleZone(villageId: string | null): string {
  const schedule = useApi(
    () =>
      settle(() =>
        api().GET("/api/villages/{village_id}/schedule", {
          params: { path: { village_id: villageId ?? "" } },
        }),
      ),
    villageId,
  );
  return safeZone(schedule.data?.tz ?? "UTC");
}
