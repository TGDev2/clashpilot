import { Link } from "react-router";
import { api, type JournalEntryOut, settle } from "../api/client";
import { useApi } from "../api/useApi";
import { sheetHref } from "../components/gantt/lanes";
import { PageTitle, ProblemAlert, Section } from "../components/ui";
import { fr } from "../i18n/fr";
import { formatInZone } from "../lib/format";
import { safeZone } from "../lib/time";
import { useVillage } from "../state/village";

const linkClass = "text-signal underline underline-offset-2";

function typeLabel(type: string): string {
  return (fr.history.types as Record<string, string>)[type] ?? type;
}

function Entry({ entry, timezone }: { entry: JournalEntryOut; timezone: string }) {
  return (
    <li className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line/60 py-2 last:border-b-0">
      <span className="w-56 text-bone">{typeLabel(entry.type)}</span>{" "}
      <span className="font-mono text-sm text-mist">{formatInZone(entry.at, timezone)}</span>{" "}
      {entry.task_id !== null && entry.task_id !== undefined && (
        <Link to={sheetHref(entry.task_id)} className={`${linkClass} font-mono text-sm`}>
          {entry.task_id}
        </Link>
      )}{" "}
      {entry.plan_id !== null && entry.plan_id !== undefined && (
        <Link to={`/constructeurs?plan=${entry.plan_id}`} className={`${linkClass} text-sm`}>
          {fr.history.plan(entry.plan_id)}
        </Link>
      )}
    </li>
  );
}

/** The journal of the village, most recent event first, as the API sends it. */
export function History() {
  const { villageId } = useVillage();
  const journal = useApi<JournalEntryOut[]>(
    () =>
      settle(() =>
        api().GET("/api/villages/{village_id}/journal", {
          params: { path: { village_id: villageId ?? "" } },
        }),
      ),
    villageId,
  );
  // The zone of the schedule; without a schedule, UTC (said aloud below).
  const schedule = useApi(
    () =>
      settle(() =>
        api().GET("/api/villages/{village_id}/schedule", {
          params: { path: { village_id: villageId ?? "" } },
        }),
      ),
    villageId,
  );
  const timezone = safeZone(schedule.data?.tz ?? "UTC");
  const entries = journal.data;
  return (
    <>
      <PageTitle>{fr.history.title}</PageTitle>
      <div className="space-y-6">
        {villageId === null && <p className="text-mist">{fr.history.needVillage}</p>}
        <ProblemAlert problem={journal.problem} />
        {entries !== undefined && (
          <Section title={fr.history.title}>
            <p className="text-xs text-mist">{fr.now.timeIn(timezone)}</p>
            {entries.length === 0 ? (
              <p className="text-mist">{fr.history.empty}</p>
            ) : (
              <ul>
                {entries.map((entry) => (
                  <Entry key={entry.entry_id} entry={entry} timezone={timezone} />
                ))}
              </ul>
            )}
          </Section>
        )}
      </div>
    </>
  );
}
