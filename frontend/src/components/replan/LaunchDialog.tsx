import { useEffect, useId, useRef, useState } from "react";
import { type ApiProblem, api, type ReplanOut, settle } from "../../api/client";
import { fr } from "../../i18n/fr";
import { Button, inputClass, ProblemAlert } from "../ui";
import { sendEvent } from "./events";

/**
 * "Je l'ai lancée": the real launch time, typed as a local date-time. The server converts it with the
 * zone of the schedule (`to-epoch`), then the event `task_started` goes out with that epoch.
 */
export function LaunchDialog({
  villageId,
  planId,
  taskId,
  initial,
  onClose,
  onApplied,
}: {
  villageId: string;
  planId: number;
  taskId: string;
  initial: string;
  onClose: () => void;
  onApplied: (report: ReplanOut) => void;
}) {
  const fieldId = useId();
  const titleId = useId();
  const fieldRef = useRef<HTMLInputElement>(null);
  const [value, setValue] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<ApiProblem | undefined>();

  // The dialog takes the focus when it opens.
  useEffect(() => {
    fieldRef.current?.focus();
  }, []);

  // Escape closes it.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  async function validate() {
    setBusy(true);
    setProblem(undefined);
    const converted = await settle(() =>
      api().POST("/api/villages/{village_id}/schedule/to-epoch", {
        params: { path: { village_id: villageId } },
        body: { local_datetime: value },
      }),
    );
    if (!converted.ok) {
      setBusy(false);
      setProblem(converted.problem);
      return;
    }
    const outcome = await sendEvent(
      villageId,
      planId,
      { type: "task_started", task_id: taskId, at: converted.data.epoch },
      false,
    );
    setBusy(false);
    if (outcome.ok) {
      onApplied(outcome.data);
    } else {
      setProblem(outcome.problem);
    }
  }

  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-ink/70 p-4">
      <dialog
        open
        aria-modal="true"
        aria-labelledby={titleId}
        className="static w-full max-w-md space-y-4 rounded-xl border border-line bg-deck p-6 text-bone"
      >
        <h3 id={titleId} className="font-display text-xl text-bone">
          {fr.replan.launchTitle}
        </h3>
        <div className="space-y-1">
          <label htmlFor={fieldId} className="block text-xs text-mist">
            {fr.replan.realTime}
          </label>
          <input
            id={fieldId}
            ref={fieldRef}
            type="datetime-local"
            value={value}
            onChange={(event) => setValue(event.target.value)}
            className={inputClass}
          />
          <p className="text-xs text-mist">{fr.replan.realTimeHint}</p>
        </div>
        <ProblemAlert problem={problem} />
        <div className="flex flex-wrap gap-3">
          <Button isDisabled={busy || value === ""} onPress={() => void validate()}>
            {fr.replan.validate}
          </Button>
          <Button isDisabled={busy} onPress={onClose}>
            {fr.replan.cancel}
          </Button>
        </div>
      </dialog>
    </div>
  );
}
