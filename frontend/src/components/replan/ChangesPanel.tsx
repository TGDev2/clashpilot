import type { ChangeOut, ReplanOut } from "../../api/client";
import { fr } from "../../i18n/fr";
import { formatInZone } from "../../lib/format";
import { uniqueKeys } from "../../lib/keys";
import { Button } from "../ui";

/** The messages of the consequences of an event, as the server wrote them (French). */
export function ConsequenceMessages({ report }: { report: ReplanOut }) {
  const messages = report.consequences.messages;
  if (messages.length === 0) {
    return null;
  }
  const keys = uniqueKeys(messages.map((message) => message.code));
  return (
    <ul className="list-disc space-y-1 pl-5 text-sm">
      {messages.map((message, index) => (
        <li key={keys[index]}>{message.message}</li>
      ))}
    </ul>
  );
}

function Placement({
  label,
  placement,
  timezone,
}: {
  label: string;
  placement: ChangeOut["before"];
  timezone: string;
}) {
  return (
    <span>
      <span className="text-mist">{label}</span>{" "}
      <span className="font-mono">
        {placement === null ? fr.replan.absent : formatInZone(placement.start, timezone)}
      </span>
    </span>
  );
}

/** What a simulated event would change: the moved tasks before and after, and the impact. */
export function ChangesPanel({
  report,
  timezone,
  busy,
  onConfirm,
  onCancel,
}: {
  report: ReplanOut;
  timezone: string;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const keys = uniqueKeys(report.changes.map((change) => change.task_id));
  return (
    <section className="space-y-3 rounded-lg border border-line bg-deck p-3">
      <h4 className="font-display text-base text-bone">{fr.replan.whatChanges}</h4>
      <p className="text-sm">
        {report.changes.length === 0
          ? fr.replan.nothingMoved
          : fr.replan.moved(report.changes.length)}
      </p>
      {report.changes.length > 0 && (
        <ul className="space-y-1 text-sm">
          {report.changes.map((change, index) => (
            <li key={keys[index]} className="flex flex-wrap gap-x-4">
              <span className="font-mono text-bone">{change.task_id}</span>{" "}
              <Placement label={fr.replan.before} placement={change.before} timezone={timezone} />{" "}
              <Placement label={fr.replan.after} placement={change.after} timezone={timezone} />
            </li>
          ))}
        </ul>
      )}
      <div>
        <h5 className="text-xs font-medium tracking-wide text-mist uppercase">
          {fr.replan.impact}
        </h5>
        <ConsequenceMessages report={report} />
      </div>
      <div className="flex flex-wrap gap-3">
        <Button isDisabled={busy} onPress={onConfirm}>
          {fr.replan.confirm}
        </Button>
        <Button isDisabled={busy} onPress={onCancel}>
          {fr.replan.cancel}
        </Button>
      </div>
    </section>
  );
}
