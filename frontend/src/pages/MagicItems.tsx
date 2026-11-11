import { useState } from "react";
import { Link } from "react-aria-components";
import { type ApiProblem, api, settle } from "../api/client";
import type { components } from "../api/schema";
import { TextInput } from "../components/forms";
import { ProblemNotice } from "../components/ProblemNotice";
import { ProofLabel } from "../components/ProofLabel";
import { ProvenanceBadge } from "../components/ProvenanceBadge";
import { Button, PageTitle, Section, TextAlert } from "../components/ui";
import { fr } from "../i18n/fr";
import { formatNumber, formatSigned, resourceLabel } from "../lib/format";
import { uniqueKeys } from "../lib/keys";
import { useVillage } from "../state/village";

type Comparison = components["schemas"]["ComparisonOut"];
type GemsComparison = components["schemas"]["GemsComparisonOut"];
type Option = components["schemas"]["OptionOut"];
type HelperOption = components["schemas"]["HelperOptionOut"];
type Consequence = components["schemas"]["ConsequenceOut"];
type Reason = components["schemas"]["ReasonOut"];

const cell = "px-3 py-2 align-top";

function ScoreCell({ delta }: { delta: number | null | undefined }) {
  return (
    <td className={cell}>
      {delta === null || delta === undefined ? (
        <ProvenanceBadge status="unknown" />
      ) : (
        <span className="font-mono">{formatSigned(delta)}</span>
      )}
    </td>
  );
}

function Consequences({ items, extra }: { items: readonly Consequence[]; extra?: string }) {
  const keys = uniqueKeys(
    items.map((item) => `${item.code}-${item.task_id ?? item.resource ?? ""}`),
  );
  return (
    <td className={cell}>
      {extra !== undefined && <p className="font-mono text-xs text-mist">{extra}</p>}
      <ul className="space-y-1 text-xs text-mist">
        {items.map((item, index) => (
          <li key={keys[index]}>{item.message}</li>
        ))}
      </ul>
    </td>
  );
}

function Reasons({ reasons }: { reasons: readonly Reason[] }) {
  return (
    <ul className="list-disc space-y-1 pl-5 text-sm text-mist">
      {reasons.map((reason) => (
        <li key={reason.code}>{reason.message}</li>
      ))}
    </ul>
  );
}

function Head({ names }: { names: readonly string[] }) {
  return (
    <thead>
      <tr className="text-xs text-mist">
        {names.map((name) => (
          <th key={name} scope="col" className={`${cell} font-medium`}>
            {name}
          </th>
        ))}
      </tr>
    </thead>
  );
}

function OptionsTable({ options }: { options: readonly Option[] }) {
  const columns = fr.magic.columns;
  const keys = uniqueKeys(options.map((option) => `${option.kind}-${option.task_id ?? ""}`));
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <Head names={[columns.option, columns.task, columns.score, columns.consequences]} />
        <tbody>
          {options.map((option, index) => (
            <tr key={keys[index]} className="border-t border-line/60">
              <td className={`${cell} text-bone`}>{fr.magic.kinds[option.kind]}</td>
              <td className={`${cell} font-mono`}>{option.task_id ?? "—"}</td>
              <ScoreCell delta={option.score_delta} />
              <Consequences
                items={option.consequences}
                extra={
                  option.gems === null || option.gems === undefined
                    ? undefined
                    : fr.magic.gems(option.gems)
                }
              />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function HelpersTable({ options }: { options: readonly HelperOption[] }) {
  const columns = fr.magic.columns;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <Head
          names={[columns.helper, columns.level, columns.cost, columns.score, columns.consequences]}
        />
        <tbody>
          {options.map((option) => (
            <tr key={`${option.data_id}-${option.to_level}`} className="border-t border-line/60">
              <td className={`${cell} text-bone`}>{fr.common.unnamed(option.data_id)}</td>
              <td className={cell}>{fr.dashboard.toLevel(option.to_level)}</td>
              <td className={`${cell} font-mono text-xs`}>
                {Object.entries(option.cost)
                  .map(([resource, amount]) => `${resourceLabel(resource)} ${formatNumber(amount)}`)
                  .join(", ")}
              </td>
              <ScoreCell delta={option.score_delta} />
              <Consequences items={option.consequences} />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** One answer of the advisor: its status, then the ranked options and the proof when it is `ok`. */
function Answer({ answer }: { answer: Comparison }) {
  if (answer.status !== "ok") {
    return (
      <div className="space-y-2">
        <p className="font-medium text-unknown">
          {answer.status === "unsupported" ? fr.magic.unsupported : fr.magic.unavailable}
        </p>
        <Reasons reasons={answer.reasons} />
      </div>
    );
  }
  return (
    <div className="space-y-3">
      {answer.scope !== "" && (
        <p className="text-sm text-mist">
          {fr.magic.scope} : {answer.scope}
        </p>
      )}
      {answer.proof !== null && answer.proof !== undefined && (
        <p className="text-sm">
          <ProofLabel proof={answer.proof} />
        </p>
      )}
      <OptionsTable options={answer.options} />
    </div>
  );
}

function Helpers({ helpers }: { helpers: GemsComparison["helpers"] }) {
  return (
    <Section title={fr.magic.helpers}>
      {helpers.status === "ok" ? (
        <HelpersTable options={helpers.options} />
      ) : (
        <div className="space-y-2">
          <p className="font-medium text-unknown">{fr.magic.unsupported}</p>
          <Reasons reasons={helpers.reasons} />
        </div>
      )}
    </Section>
  );
}

/** Rank keeping an item or the gems against using them, from the engine's simulated scenarios. */
export function MagicItems() {
  const { villageId } = useVillage();
  const [item, setItem] = useState("");
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState<string | undefined>();
  const [problem, setProblem] = useState<ApiProblem | undefined>();
  const [itemAnswer, setItemAnswer] = useState<Comparison | undefined>();
  const [gemsAnswer, setGemsAnswer] = useState<GemsComparison | undefined>();

  async function compareItem() {
    setLocalError(undefined);
    setProblem(undefined);
    if (villageId === null) {
      setLocalError(fr.magic.needVillage);
      return;
    }
    if (item.trim() === "") {
      setLocalError(fr.magic.needItem);
      return;
    }
    setBusy(true);
    const outcome = await settle(() =>
      api().POST("/api/villages/{village_id}/items/compare", {
        params: { path: { village_id: villageId } },
        body: { item: item.trim() },
      }),
    );
    setBusy(false);
    if (outcome.ok) {
      setItemAnswer(outcome.data);
    } else {
      setProblem(outcome.problem);
    }
  }

  async function compareGems() {
    setLocalError(undefined);
    setProblem(undefined);
    if (villageId === null) {
      setLocalError(fr.magic.needVillage);
      return;
    }
    setBusy(true);
    const outcome = await settle(() =>
      api().POST("/api/villages/{village_id}/gems/compare", {
        params: { path: { village_id: villageId } },
        body: {},
      }),
    );
    setBusy(false);
    if (outcome.ok) {
      setGemsAnswer(outcome.data);
    } else {
      setProblem(outcome.problem);
    }
  }

  return (
    <>
      <PageTitle>{fr.magic.title}</PageTitle>
      <div className="space-y-6">
        <p className="text-sm text-mist">{fr.magic.intro}</p>
        {villageId === null && (
          <p className="text-mist">
            <Link href="/importer" className="text-signal underline underline-offset-2">
              {fr.dashboard.noVillageLink}
            </Link>
          </p>
        )}
        <Section title={fr.magic.compareTitle}>
          <TextInput
            label={fr.magic.item}
            hint={fr.magic.itemHint}
            value={item}
            onChange={setItem}
          />
          <div className="flex flex-wrap gap-3">
            <Button onPress={() => void compareItem()} isDisabled={busy}>
              {fr.magic.compare}
            </Button>
            <Button onPress={() => void compareGems()} isDisabled={busy}>
              {fr.magic.compareGems}
            </Button>
          </div>
          {localError !== undefined && <TextAlert>{localError}</TextAlert>}
          <ProblemNotice problem={problem} />
        </Section>
        {itemAnswer !== undefined && (
          <Section title={fr.magic.itemResult}>
            <Answer answer={itemAnswer} />
          </Section>
        )}
        {gemsAnswer !== undefined && (
          <>
            <Section title={fr.magic.gemsResult}>
              <Answer answer={gemsAnswer} />
            </Section>
            <Helpers helpers={gemsAnswer.helpers} />
          </>
        )}
      </div>
    </>
  );
}
