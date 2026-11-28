import { api, type Base, settle } from "../../api/client";
import { useApi } from "../../api/useApi";
import { fr } from "../../i18n/fr";
import { useVillage } from "../../state/village";

/** The export section that lists the troops of each base. */
const TROOP_SECTION: Record<Base, string> = { home: "units", builder: "units2" };

/**
 * The troops of the chosen base, as the village state names them, each with the identifier the
 * composition takes. The list is only a help: a state that cannot be read shows nothing.
 */
export function Troops({ villageId }: { villageId: string }) {
  const { base } = useVillage();
  const state = useApi(
    () =>
      settle(() =>
        api().GET("/api/villages/{village_id}/state", {
          params: { path: { village_id: villageId } },
        }),
      ),
    villageId,
  );
  // One entry per troop, whatever the number of rows of the export.
  const troops = new Map(
    (state.data?.items ?? [])
      .filter((item) => item.section === TROOP_SECTION[base])
      .map((item) => [item.data_id, item.name ?? fr.common.unnamed(item.data_id)]),
  );
  if (troops.size === 0) {
    return null;
  }
  return (
    <div className="space-y-2">
      <h3 className="text-xs font-medium tracking-wide text-mist uppercase">
        {fr.strategy.troops}
      </h3>
      <ul className="grid gap-x-6 sm:grid-cols-2">
        {[...troops].map(([dataId, name]) => (
          <li
            key={dataId}
            className="flex items-baseline justify-between gap-3 border-b border-line/60 py-1 text-sm"
          >
            <span className="text-bone">{name}</span>{" "}
            <span className="font-mono text-xs text-mist tabular-nums">{dataId}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
