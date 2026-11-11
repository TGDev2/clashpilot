import { useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { type ApiProblem, api, settle } from "../api/client";
import type { components } from "../api/schema";
import { useApi } from "../api/useApi";
import { TextInput } from "../components/forms";
import { ProblemNotice } from "../components/ProblemNotice";
import { Button, PageTitle, TextAlert } from "../components/ui";
import { fr } from "../i18n/fr";
import { formatDuration, formatNumber, resourceLabel } from "../lib/format";

type Item = components["schemas"]["ItemLevels"];
type Level = components["schemas"]["LevelOut"];

function wholeNumber(text: string): number | undefined {
  return /^\d+$/.test(text.trim()) ? Number(text) : undefined;
}

/** The correction form of one level; it starts from the values the API shows. */
function Correction({
  packId,
  dataId,
  level,
  onSaved,
}: {
  packId: string;
  dataId: number;
  level: Level;
  onSaved: (item: Item) => void;
}) {
  const [duration, setDuration] = useState(String(level.duration_s));
  const [costs, setCosts] = useState<Record<string, string>>(() =>
    Object.fromEntries(Object.entries(level.cost).map(([k, v]) => [k, String(v)])),
  );
  const [note, setNote] = useState(level.note ?? "");
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState<string | undefined>();
  const [problem, setProblem] = useState<ApiProblem | undefined>();

  // The level changes when a correction comes back: the form shows what is now stored.
  useEffect(() => {
    setDuration(String(level.duration_s));
    setCosts(Object.fromEntries(Object.entries(level.cost).map(([k, v]) => [k, String(v)])));
    setNote(level.note ?? "");
  }, [level]);

  async function save() {
    setLocalError(undefined);
    setProblem(undefined);
    const seconds = wholeNumber(duration);
    const cost: Record<string, number> = {};
    for (const [resource, text] of Object.entries(costs)) {
      const amount = wholeNumber(text);
      if (amount === undefined) {
        setLocalError(fr.levelSheet.invalid);
        return;
      }
      cost[resource] = amount;
    }
    if (seconds === undefined) {
      setLocalError(fr.levelSheet.invalid);
      return;
    }
    setBusy(true);
    const outcome = await settle(() =>
      api().PATCH("/api/gamedata/packs/{pack_id}/items/{data_id}/levels/{level}", {
        params: { path: { pack_id: packId, data_id: dataId, level: level.level } },
        body: { duration_s: seconds, cost, ...(note.trim() === "" ? {} : { note: note.trim() }) },
      }),
    );
    setBusy(false);
    if (outcome.ok) {
      onSaved(outcome.data);
    } else {
      setProblem(outcome.problem);
    }
  }

  return (
    <div className="space-y-4">
      <TextInput
        label={fr.levelSheet.duration}
        type="number"
        min={0}
        value={duration}
        onChange={setDuration}
      />
      <div className="grid gap-4 sm:grid-cols-2">
        {Object.keys(costs).map((resource) => (
          <TextInput
            key={resource}
            label={fr.levelSheet.cost(resourceLabel(resource))}
            type="number"
            min={0}
            value={costs[resource] ?? ""}
            onChange={(value) => setCosts((previous) => ({ ...previous, [resource]: value }))}
          />
        ))}
      </div>
      <TextInput label={fr.levelSheet.note} value={note} onChange={setNote} />
      <Button onPress={() => void save()} isDisabled={busy}>
        {busy ? fr.levelSheet.saving : fr.levelSheet.save}
      </Button>
      {localError !== undefined && <TextAlert>{localError}</TextAlert>}
      <ProblemNotice problem={problem} />
    </div>
  );
}

function LevelView({
  item,
  level,
  packId,
  onSaved,
}: {
  item: Item;
  level: Level;
  packId: string;
  onSaved: (item: Item) => void;
}) {
  const original = level.original;
  return (
    <article className="space-y-5 rounded-xl border border-line bg-deck p-4 sm:p-6">
      <header className="space-y-1">
        <h3 className="font-display text-2xl text-bone">{item.name}</h3>
        <p className="text-sm text-mist">{fr.common.level(level.level)}</p>
        <p className="text-sm text-mist">
          {fr.levelSheet.source} : {level.source.label}
        </p>
        {original !== null && original !== undefined && (
          <p className="inline-block rounded-full border border-estimated/60 px-2 py-0.5 text-xs font-medium text-estimated">
            {fr.levelSheet.manual}
          </p>
        )}
      </header>
      {original !== null && original !== undefined && (
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
          <dt className="text-mist">{fr.levelSheet.imported}</dt>
          <dd className="font-mono text-bone">
            {fr.levelSheet.importedDuration} {formatDuration(original.duration_s)}
            {Object.entries(original.cost).map(([resource, amount]) => (
              <span key={resource}>
                {" "}
                · {resourceLabel(resource)} {formatNumber(amount)}
              </span>
            ))}
          </dd>
        </dl>
      )}
      {level.note !== null && level.note !== undefined && (
        <p className="text-sm">
          <span className="text-mist">{fr.levelSheet.noteLabel} : </span>
          <span className="text-bone">{level.note}</span>
        </p>
      )}
      <Correction packId={packId} dataId={item.data_id} level={level} onSaved={onSaved} />
    </article>
  );
}

/** The sheet of one level of a pack item, with its manual correction (duration, cost, note). */
export function LevelSheet() {
  const { packId = "", dataId = "", level = "" } = useParams();
  const id = wholeNumber(dataId);
  const number = wholeNumber(level);
  const loaded = useApi<Item>(
    () =>
      settle(() =>
        api().GET("/api/gamedata/packs/{pack_id}/items/{data_id}", {
          params: { path: { pack_id: packId, data_id: id ?? 0 } },
        }),
      ),
    id === undefined ? null : `${packId}:${id}`,
  );
  const [saved, setSaved] = useState<Item | undefined>();
  const item = saved ?? loaded.data;
  const row = item?.levels.find((candidate) => candidate.level === number);
  return (
    <>
      <PageTitle>{fr.levelSheet.title}</PageTitle>
      <div className="space-y-6">
        {loaded.loading && item === undefined && <p className="text-mist">{fr.common.loading}</p>}
        <ProblemNotice problem={loaded.problem} />
        {item !== undefined && row === undefined && (
          <p className="text-mist">{fr.common.unknownLevel}</p>
        )}
        {item !== undefined && row !== undefined && (
          <LevelView item={item} level={row} packId={packId} onSaved={setSaved} />
        )}
      </div>
    </>
  );
}

/** Every level of a pack item, each linked to its own sheet; a corrected level carries the mention. */
export function ItemLevelsPage() {
  const { packId = "", dataId = "" } = useParams();
  const id = wholeNumber(dataId);
  const loaded = useApi<Item>(
    () =>
      settle(() =>
        api().GET("/api/gamedata/packs/{pack_id}/items/{data_id}", {
          params: { path: { pack_id: packId, data_id: id ?? 0 } },
        }),
      ),
    id === undefined ? null : `${packId}:${id}`,
  );
  const item = loaded.data;
  return (
    <>
      <PageTitle>{fr.levelSheet.levelsTitle}</PageTitle>
      <div className="space-y-6">
        {loaded.loading && item === undefined && <p className="text-mist">{fr.common.loading}</p>}
        <ProblemNotice problem={loaded.problem} />
        {item !== undefined && (
          <section className="space-y-3">
            <h3 className="font-display text-xl text-bone">{item.name}</h3>
            <ul className="space-y-1 text-sm">
              {item.levels.map((row) => (
                <li key={row.level} className="flex flex-wrap items-center gap-x-3">
                  <Link
                    to={`/donnees/${encodeURIComponent(packId)}/${item.data_id}/${row.level}`}
                    className="text-signal underline underline-offset-2"
                  >
                    {fr.common.level(row.level)}
                  </Link>
                  {row.original !== null && row.original !== undefined && (
                    <span className="text-xs text-estimated">{fr.levelSheet.manual}</span>
                  )}
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </>
  );
}
