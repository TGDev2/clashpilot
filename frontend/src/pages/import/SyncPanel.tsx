import { useState } from "react";
import { type ApiProblem, api, type SyncOut, settle } from "../../api/client";
import { Button, ProblemAlert, Section } from "../../components/ui";
import { fr } from "../../i18n/fr";
import { uniqueKeys } from "../../lib/keys";

/** Read troop, hero, spell and equipment levels from the official API (the server holds the key). */
export function SyncPanel({ villageId }: { villageId: string | null }) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<ApiProblem | undefined>();
  const [result, setResult] = useState<SyncOut | undefined>();

  async function synchronise() {
    if (villageId === null) {
      return;
    }
    setBusy(true);
    setProblem(undefined);
    setResult(undefined);
    const outcome = await settle(() =>
      api().POST("/api/villages/{village_id}/sync", {
        params: { path: { village_id: villageId } },
      }),
    );
    setBusy(false);
    if (outcome.ok) {
      setResult(outcome.data);
    } else {
      setProblem(outcome.problem);
    }
  }

  const conflictKeys = uniqueKeys((result?.conflicts ?? []).map((c) => `${c.section}-${c.name}`));

  return (
    <Section id="synchronisation" title={fr.sync.title}>
      <p className="text-sm text-mist">{fr.sync.hint}</p>
      <Button onPress={() => void synchronise()} isDisabled={villageId === null || busy}>
        {busy ? fr.sync.running : fr.sync.button}
      </Button>
      {villageId === null && <p className="text-sm text-mist">{fr.sync.needVillage}</p>}
      <ProblemAlert problem={problem} />
      {result && (
        <div className="space-y-3 rounded-lg border border-line bg-ink p-4">
          <p className="font-mono text-sm"> {fr.sync.updated(result.updated.length)} </p>
          <div>
            <h3 className="text-sm font-medium">{fr.sync.conflicts}</h3>
            {result.conflicts.length === 0 ? (
              <p className="text-sm text-mist">{fr.sync.noConflicts}</p>
            ) : (
              <ul className="space-y-1 text-sm">
                {result.conflicts.map((conflict, position) => (
                  <li key={conflictKeys[position]} className="flex flex-wrap gap-x-3">
                    <span className="text-bone">{conflict.name}</span>
                    {conflict.level !== null && (
                      <span className="font-mono text-mist">{fr.common.level(conflict.level)}</span>
                    )}
                    <span className="text-estimated">{conflict.reason}</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </Section>
  );
}
