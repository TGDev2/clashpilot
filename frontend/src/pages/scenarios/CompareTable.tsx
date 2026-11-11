import type { ReactNode } from "react";
import type { components } from "../../api/schema";
import { ProofLabel } from "../../components/ProofLabel";
import { ProvenanceBadge } from "../../components/ProvenanceBadge";
import { Section } from "../../components/ui";
import { fr } from "../../i18n/fr";
import { formatDuration, formatInZone, formatNumber, resourceLabel } from "../../lib/format";

type Compare = components["schemas"]["CompareOut"];
type Figures =
  | components["schemas"]["PlanFiguresOut"]
  | components["schemas"]["ScenarioFiguresOut"];

const cell = "px-3 py-2 align-top";

/** A figure the API did not compute is the Inconnu badge, never a 0. */
function Figure<T>({
  value,
  children,
}: {
  value: T | null | undefined;
  children: (v: T) => ReactNode;
}) {
  return (
    <td className={cell}>
      {value === null || value === undefined ? (
        <ProvenanceBadge status="unknown" />
      ) : (
        children(value)
      )}
    </td>
  );
}

function FigureCells({ figures, timezone }: { figures: Figures; timezone: string }) {
  // `logins` is in the answer but kept out of the OpenAPI schema (E05 guard).
  const logins = (figures as { logins?: number | null }).logins;
  return (
    <>
      <Figure value={figures.goal_finish}>
        {(epoch) => <span className="font-mono">{formatInZone(epoch, timezone)}</span>}
      </Figure>
      <Figure value={figures.finished_count}>
        {(count) => <span className="font-mono">{formatNumber(count)}</span>}
      </Figure>
      <Figure value={figures.remaining_cost}>
        {(cost) => (
          <ul className="font-mono text-xs">
            {Object.entries(cost).map(([resource, amount]) => (
              <li key={resource}>
                {resourceLabel(resource)} {formatNumber(amount)}
              </li>
            ))}
          </ul>
        )}
      </Figure>
      <Figure value={figures.value_gained}>
        {(value) => <span className="font-mono">{formatNumber(value)}</span>}
      </Figure>
      <Figure value={figures.hero_unavailable_s}>
        {(seconds) => <span className="font-mono">{formatDuration(seconds)}</span>}
      </Figure>
      <Figure value={logins}>
        {(count) => <span className="font-mono">{formatNumber(count)}</span>}
      </Figure>
      <Figure value={figures.idle_builder_s}>
        {(seconds) => <span className="font-mono">{formatDuration(seconds)}</span>}
      </Figure>
      <Figure value={figures.proof}>{(proof) => <ProofLabel proof={proof} />}</Figure>
    </>
  );
}

/** The real plan and the chosen scenarios side by side, one column per figure of the API. */
export function CompareTable({ comparison, timezone }: { comparison: Compare; timezone: string }) {
  const columns = Object.values(fr.scenarios.columns);
  return (
    <Section title={fr.scenarios.compareTitle}>
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="text-xs text-mist">
              {columns.map((column) => (
                <th key={column} scope="col" className={`${cell} font-medium`}>
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            <tr className="border-t border-line/60">
              <th scope="row" className={`${cell} font-medium text-bone`}>
                {fr.scenarios.realPlan}
              </th>
              <FigureCells figures={comparison.plan} timezone={timezone} />
            </tr>
            {comparison.scenarios.map((scenario) => (
              <tr key={scenario.scenario_id} className="border-t border-line/60">
                <th scope="row" className={`${cell} font-medium text-bone`}>
                  {scenario.name}
                </th>
                <FigureCells figures={scenario} timezone={timezone} />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}
