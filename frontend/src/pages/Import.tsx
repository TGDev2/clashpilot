import { useEffect, useState } from "react";
import { Label, TextArea, TextField } from "react-aria-components";
import { useLocation } from "react-router";
import type { ApiProblem } from "../api/client";
import { api, settle, type VillageState } from "../api/client";
import { Button, inputClass, PageTitle, ProblemAlert, Section, TextAlert } from "../components/ui";
import { fr } from "../i18n/fr";
import { useVillage } from "../state/village";
import { Changes } from "./import/Changes";
import { ManualForm } from "./import/ManualForm";
import { PacksPanel } from "./import/PacksPanel";
import { SyncPanel } from "./import/SyncPanel";

function Summary({ state }: { state: VillageState }) {
  return (
    <div className="space-y-3 rounded-lg border border-line bg-ink p-4">
      <h3 className="font-display text-lg">{fr.importPage.summaryTitle}</h3>
      <ul className="flex flex-wrap gap-x-6 gap-y-1 font-mono text-sm">
        <li>{fr.importPage.items(state.items.length)} </li>
        <li>{fr.importPage.running(state.running.length)} </li>
        <li>{fr.importPage.unknownIds(state.unknown_ids.length)} </li>
      </ul>
      {state.unknown_ids.length > 0 && (
        <p className="text-xs text-mist">
          {fr.importPage.unknownIdsList}{" "}
          <span className="font-mono">{state.unknown_ids.join(", ")}</span>
        </p>
      )}
      <div>
        <h4 className="text-sm font-medium">{fr.importPage.warnings}</h4>
        {state.warnings.length === 0 ? (
          <p className="text-sm text-mist">{fr.importPage.noWarnings}</p>
        ) : (
          <ul className="list-disc space-y-1 pl-5 text-sm text-estimated">
            {state.warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function ExportSection({ onImported }: { onImported: (state: VillageState) => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<ApiProblem | undefined>();
  const [localError, setLocalError] = useState<string | undefined>();
  const [summary, setSummary] = useState<VillageState | undefined>();

  async function submit() {
    setProblem(undefined);
    setLocalError(undefined);
    if (text.trim() === "") {
      setLocalError(fr.importPage.pasteFirst);
      return;
    }
    try {
      JSON.parse(text);
    } catch {
      setLocalError(fr.importPage.invalidJson);
      return;
    }
    setBusy(true);
    // The raw text goes out untouched: the server hashes it to recognise a repeated import.
    const outcome = await settle(() =>
      api().POST("/api/villages/import", { body: {}, bodySerializer: () => text }),
    );
    setBusy(false);
    if (outcome.ok) {
      setSummary(outcome.data);
      onImported(outcome.data);
    } else {
      setProblem(outcome.problem);
    }
  }

  return (
    <Section id="import" title={fr.importPage.exportTitle}>
      <TextField value={text} onChange={setText} className="block">
        <Label className="mb-1 block text-sm text-bone">{fr.importPage.exportLabel}</Label>
        <TextArea rows={8} spellCheck={false} className={`${inputClass} resize-y`} />
        <p className="mt-1 text-xs text-mist">{fr.importPage.exportHint}</p>
      </TextField>
      <Button onPress={() => void submit()} isDisabled={busy}>
        {busy ? fr.importPage.submitting : fr.importPage.submit}
      </Button>
      {localError && <TextAlert>{localError}</TextAlert>}
      <ProblemAlert problem={problem} />
      {summary && <Summary state={summary} />}
    </Section>
  );
}

export function Import() {
  const { villageId, setVillageId } = useVillage();
  const { hash } = useLocation();
  const [imports, setImports] = useState(0);

  // An in-page anchor such as /importer#packs scrolls to its section once the page is there.
  useEffect(() => {
    if (hash.length > 1) {
      document.getElementById(hash.slice(1))?.scrollIntoView?.();
    }
  }, [hash]);

  return (
    <>
      <PageTitle>{fr.importPage.title}</PageTitle>
      <div className="space-y-6">
        <ExportSection
          onImported={(state) => {
            setVillageId(state.village_id);
            setImports((n) => n + 1);
          }}
        />
        <Changes villageId={villageId} version={imports} />
        <SyncPanel villageId={villageId} />
        <ManualForm villageId={villageId} />
        <PacksPanel />
      </div>
    </>
  );
}
