import { api, type DiffOut, settle } from "../../api/client";
import { useApi } from "../../api/useApi";
import { ProblemAlert, Section } from "../../components/ui";
import { fr } from "../../i18n/fr";

type Entry = { key: string; name: string; detail: string };

function ChangeList({ title, entries }: { title: string; entries: Entry[] }) {
  if (entries.length === 0) {
    return null;
  }
  return (
    <div>
      <h3 className="mb-1 text-sm font-medium text-bone">{title}</h3>
      <ul className="space-y-1 text-sm">
        {entries.map((entry) => (
          <li key={entry.key} className="flex flex-wrap gap-x-3">
            <span className="text-bone">{entry.name}</span>
            <span className="font-mono text-mist">{entry.detail}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function nameOf(entry: { name: string | null; data_id: number }): string {
  return entry.name ?? fr.common.unnamed(entry.data_id);
}

function lists(diff: DiffOut): Array<{ title: string; entries: Entry[] }> {
  const presence = (rows: DiffOut["added"], prefix: string): Entry[] =>
    rows.map((row, index) => ({
      key: `${prefix}-${row.data_id}-${index}`,
      name: nameOf(row),
      detail: [
        row.level === null ? fr.common.unknownLevel : fr.common.level(row.level),
        row.count > 1 ? fr.dashboard.count(row.count) : "",
      ]
        .filter(Boolean)
        .join(" "),
    }));
  const jobs = (rows: DiffOut["started"], prefix: string): Entry[] =>
    rows.map((row, index) => ({
      key: `${prefix}-${row.data_id}-${index}`,
      name: nameOf(row),
      detail: fr.changes.toLevel(row.to_level),
    }));
  return [
    {
      title: fr.changes.upgraded,
      entries: diff.upgraded.map((row, index) => ({
        key: `up-${row.data_id}-${index}`,
        name: nameOf(row),
        detail: [
          fr.changes.levelChange(row.from_level, row.to_level),
          row.count > 1 ? fr.dashboard.count(row.count) : "",
        ]
          .filter(Boolean)
          .join(" "),
      })),
    },
    { title: fr.changes.started, entries: jobs(diff.started, "start") },
    { title: fr.changes.finished, entries: jobs(diff.finished, "end") },
    { title: fr.changes.added, entries: presence(diff.added, "add") },
    { title: fr.changes.removed, entries: presence(diff.removed, "rem") },
  ];
}

/** What changed between the two latest imports of the current village. */
export function Changes({ villageId, version }: { villageId: string | null; version: number }) {
  const diff = useApi(
    () =>
      settle(() =>
        api().GET("/api/villages/{village_id}/diff", {
          params: { path: { village_id: villageId ?? "" } },
        }),
      ),
    villageId === null ? null : `${villageId}:${version}`,
  );
  const needsTwo = villageId === null || diff.problem?.code === "diff_requires_two_snapshots";
  const sections = diff.data ? lists(diff.data).filter((s) => s.entries.length > 0) : [];

  return (
    <Section id="changements" title={fr.changes.title}>
      {needsTwo && <p className="text-mist">{fr.changes.needTwo}</p>}
      {!needsTwo && <ProblemAlert problem={diff.problem} />}
      {diff.data && sections.length === 0 && <p className="text-mist">{fr.changes.none}</p>}
      {diff.data &&
        sections.map((s) => <ChangeList key={s.title} title={s.title} entries={s.entries} />)}
    </Section>
  );
}
