import { useId, useState } from "react";
import type { ActionOut, ApiProblem, ReplanOut } from "../../api/client";
import { fr } from "../../i18n/fr";
import { localDateTimeValue, safeZone } from "../../lib/time";
import { Button, inputClass, ProblemAlert } from "../ui";
import { ChangesPanel } from "./ChangesPanel";
import { type ReplanEvent, sendEvent } from "./events";
import { LaunchDialog } from "./LaunchDialog";

const MINUTE_S = 60;

/**
 * The buttons of one `start` action: "Je l'ai lancée" (the real launch) and "Simuler un retard"
 * (a late start, answered as a dry run until the user confirms). The replan is the server's: this
 * only sends the event and shows its answer.
 */
export function ActionControls({
  villageId,
  planId,
  action,
  now,
  timezone,
  onApplied,
}: {
  villageId: string;
  planId: number;
  action: ActionOut;
  now: number;
  timezone: string;
  onApplied: (report: ReplanOut) => void;
}) {
  const freeId = useId();
  const [launching, setLaunching] = useState(false);
  const [choosing, setChoosing] = useState(false);
  const [free, setFree] = useState("");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<ApiProblem | undefined>();
  const [simulation, setSimulation] = useState<{ event: ReplanEvent; report: ReplanOut }>();
  if (action.task_id === null || action.task_id === undefined) {
    return null;
  }
  const taskId: string = action.task_id;

  async function simulate(delaySeconds: number) {
    const event: ReplanEvent = {
      type: "task_started",
      task_id: taskId,
      at: action.at + delaySeconds,
    };
    setBusy(true);
    setProblem(undefined);
    const outcome = await sendEvent(villageId, planId, event, true);
    setBusy(false);
    if (outcome.ok) {
      setSimulation({ event, report: outcome.data });
    } else {
      setSimulation(undefined);
      setProblem(outcome.problem);
    }
  }

  async function confirm() {
    if (simulation === undefined) {
      return;
    }
    setBusy(true);
    setProblem(undefined);
    const outcome = await sendEvent(villageId, planId, simulation.event, false);
    setBusy(false);
    if (outcome.ok) {
      onApplied(outcome.data);
    } else {
      setSimulation(undefined);
      setProblem(outcome.problem);
    }
  }

  function cancel() {
    setSimulation(undefined);
    setChoosing(false);
    setFree("");
  }

  const minutes = /^\d+$/.test(free.trim()) ? Number(free.trim()) : 0;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-3">
        <Button isDisabled={busy} onPress={() => setLaunching(true)}>
          {fr.replan.launched}
        </Button>
        <Button isDisabled={busy} onPress={() => setChoosing(true)}>
          {fr.replan.simulateDelay}
        </Button>
      </div>
      {launching && (
        <LaunchDialog
          villageId={villageId}
          planId={planId}
          taskId={taskId}
          initial={localDateTimeValue(now, safeZone(timezone))}
          onClose={() => setLaunching(false)}
          onApplied={(report) => {
            setLaunching(false);
            onApplied(report);
          }}
        />
      )}
      {choosing && simulation === undefined && (
        <div className="flex flex-wrap items-end gap-3">
          <Button isDisabled={busy} onPress={() => void simulate(15 * MINUTE_S)}>
            {fr.replan.delayQuarter}
          </Button>
          <Button isDisabled={busy} onPress={() => void simulate(60 * MINUTE_S)}>
            {fr.replan.delayHour}
          </Button>
          <div className="space-y-1">
            <label htmlFor={freeId} className="block text-xs text-mist">
              {fr.replan.delayFree}
            </label>
            <input
              id={freeId}
              type="number"
              min={1}
              inputMode="numeric"
              value={free}
              onChange={(event) => setFree(event.target.value)}
              className={`${inputClass} w-32`}
            />
          </div>
          <Button
            isDisabled={busy || minutes <= 0}
            onPress={() => void simulate(minutes * MINUTE_S)}
          >
            {fr.replan.simulate}
          </Button>
          <Button isDisabled={busy} onPress={cancel}>
            {fr.replan.cancel}
          </Button>
        </div>
      )}
      <ProblemAlert problem={problem} />
      {simulation !== undefined && (
        <ChangesPanel
          report={simulation.report}
          timezone={timezone}
          busy={busy}
          onConfirm={() => void confirm()}
          onCancel={cancel}
        />
      )}
    </div>
  );
}
