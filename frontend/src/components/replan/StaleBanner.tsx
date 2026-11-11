import { useState } from "react";
import type { ApiProblem } from "../../api/client";
import { fr } from "../../i18n/fr";
import { Banner, Button, ProblemAlert } from "../ui";
import { sendEvent } from "./events";

/** A newer export than the plan's: the button replans on it with the event `snapshot_imported`. */
export function StaleBanner({
  villageId,
  planId,
  onReplanned,
}: {
  villageId: string;
  planId: number;
  onReplanned: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<ApiProblem | undefined>();

  async function replan() {
    setBusy(true);
    setProblem(undefined);
    const outcome = await sendEvent(villageId, planId, { type: "snapshot_imported" }, false);
    setBusy(false);
    if (outcome.ok) {
      onReplanned();
    } else {
      setProblem(outcome.problem);
    }
  }

  return (
    <div className="space-y-3">
      <Banner>
        <span className="mr-3">{fr.dashboard.stale}</span>
        <Button isDisabled={busy} onPress={() => void replan()}>
          {busy ? fr.dashboard.recomputing : fr.dashboard.recompute}
        </Button>
      </Banner>
      <ProblemAlert problem={problem} />
    </div>
  );
}
