import type { ProofOut } from "../api/client";
import { fr } from "../i18n/fr";
import { formatNumber } from "../lib/format";

/**
 * What the engine proved about a plan, in words. The only place that may say "optimale": a
 * heuristic or merely feasible plan must never read as optimal.
 */
export function proofText(proof: ProofOut): string {
  switch (proof.kind) {
    case "heuristic":
      return fr.proof.heuristic;
    case "feasible":
      return proof.gap_abs === null || proof.gap_abs === undefined
        ? fr.proof.feasibleNoGap
        : `${fr.proof.feasible} : ${formatNumber(proof.gap_abs)}`;
    case "optimal_window":
      return proof.scope === undefined || proof.scope === ""
        ? fr.proof.optimalWindow
        : `${fr.proof.optimalWindow} : ${proof.scope}`;
  }
}

export function ProofLabel({ proof }: { proof: ProofOut }) {
  return <span className="font-medium text-bone">{proofText(proof)}</span>;
}
