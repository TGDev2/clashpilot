import { type ChangeEvent, useState } from "react";
import { type ApiProblem, api, settle } from "../../api/client";
import { useApi } from "../../api/useApi";
import { Button, inputClass, ProblemAlert, Section, TextAlert } from "../../components/ui";
import { fr } from "../../i18n/fr";

/** Game data packs: import a JSON file, list what is stored, choose the active one. */
export function PacksPanel() {
  const packs = useApi(() => settle(() => api().GET("/api/gamedata/packs")), "packs");
  const [problem, setProblem] = useState<ApiProblem | undefined>();
  const [localError, setLocalError] = useState<string | undefined>();
  const [notice, setNotice] = useState<string | undefined>();

  function reset() {
    setProblem(undefined);
    setLocalError(undefined);
    setNotice(undefined);
  }

  async function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const input = event.currentTarget;
    const file = input.files?.[0];
    if (!file) {
      return;
    }
    reset();
    let pack: Record<string, unknown>;
    try {
      pack = JSON.parse(await file.text());
    } catch {
      setLocalError(fr.packs.invalidJson);
      input.value = "";
      return;
    }
    const outcome = await settle(() => api().POST("/api/gamedata/packs", { body: pack }));
    input.value = "";
    if (outcome.ok) {
      setNotice(fr.packs.imported);
      packs.reload();
    } else {
      setProblem(outcome.problem);
    }
  }

  async function activate(packId: string) {
    reset();
    const outcome = await settle(() =>
      api().PUT("/api/gamedata/packs/{pack_id}/active", { params: { path: { pack_id: packId } } }),
    );
    if (outcome.ok) {
      setNotice(fr.packs.activated);
      packs.reload();
    } else {
      setProblem(outcome.problem);
    }
  }

  return (
    <Section id="packs" title={fr.packs.title}>
      <p className="text-sm text-mist">{fr.packs.hint}</p>
      <div>
        <label htmlFor="pack-file" className="mb-1 block text-sm text-bone">
          {fr.packs.fileLabel}
        </label>
        <input
          id="pack-file"
          type="file"
          accept="application/json,.json"
          onChange={(event) => void chooseFile(event)}
          className={`${inputClass} file:mr-3 file:rounded file:border-0 file:bg-deck-hi file:px-3 file:py-1 file:text-bone`}
        />
      </div>
      {notice && (
        <p role="status" className="text-sm text-confirmed">
          {notice}
        </p>
      )}
      {localError && <TextAlert>{localError}</TextAlert>}
      <ProblemAlert problem={problem} />
      <ProblemAlert problem={packs.problem} />
      <div>
        <h3 className="mb-1 text-sm font-medium">{fr.packs.list}</h3>
        {packs.data?.length === 0 && <p className="text-sm text-mist">{fr.packs.empty}</p>}
        <ul>
          {packs.data?.map((pack) => (
            <li
              key={pack.pack_id}
              className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line/60 py-2 last:border-b-0"
            >
              <span className="font-mono text-sm text-bone">{pack.pack_id}</span>{" "}
              <span className="text-sm text-mist">{fr.packs.version(pack.game_version)}</span>{" "}
              <span className="text-sm text-mist">{fr.packs.date(pack.as_of)}</span>{" "}
              {pack.fictional && (
                <span className="rounded-full border border-estimated/60 px-2 py-0.5 text-xs text-estimated">
                  {fr.packs.fictional}
                </span>
              )}{" "}
              {pack.active ? (
                <span className="rounded-full border border-confirmed/60 px-2 py-0.5 text-xs text-confirmed">
                  {fr.packs.active}
                </span>
              ) : (
                <Button onPress={() => void activate(pack.pack_id)} className="ml-auto">
                  {fr.packs.activate}
                </Button>
              )}
            </li>
          ))}
        </ul>
      </div>
    </Section>
  );
}
