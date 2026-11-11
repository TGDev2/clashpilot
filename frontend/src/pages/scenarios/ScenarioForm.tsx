import { useState } from "react";
import { type ApiProblem, api, type Outcome, settle } from "../../api/client";
import type { components } from "../../api/schema";
import { useApi } from "../../api/useApi";
import { SelectInput, TextInput } from "../../components/forms";
import { ProblemNotice } from "../../components/ProblemNotice";
import { Button, Section, TextAlert } from "../../components/ui";
import { fr } from "../../i18n/fr";

type Overlay = components["schemas"]["ScenarioRequest"]["overlays"][number];
type Kind = Overlay["type"];
type Mode = components["schemas"]["PolicyIn"]["mode"];

const KINDS = Object.keys(fr.scenarios.overlays) as Kind[];
const MODES = Object.keys(fr.scenarios.modes) as Mode[];

function integerOf(text: string | undefined, min: number): number | undefined {
  const value = (text ?? "").trim();
  return /^\d+$/.test(value) && Number(value) >= min ? Number(value) : undefined;
}

/** A local date-time input value (`AAAA-MM-JJTHH:MM`) with the seconds the API asks for. */
function withSeconds(text: string | undefined): string | undefined {
  const value = (text ?? "").trim();
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value)) {
    return `${value}:00`;
  }
  return /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$/.test(value) ? value : undefined;
}

type Built = { overlay: Overlay } | { problem: ApiProblem } | undefined;

/** A form creating one scenario with one overlay; the server computes and stores it. */
export function ScenarioForm({
  villageId,
  onCreated,
}: {
  villageId: string | null;
  onCreated: () => void;
}) {
  const strategies = useApi(() => settle(() => api().GET("/api/meta/strategies")), "strategies");
  const [name, setName] = useState("");
  const [kind, setKind] = useState<Kind>("start_now");
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [localError, setLocalError] = useState<string | undefined>();
  const [problem, setProblem] = useState<ApiProblem | undefined>();

  const set = (key: string) => (value: string) =>
    setValues((previous) => ({ ...previous, [key]: value }));
  const get = (key: string) => values[key] ?? "";

  function chooseKind(next: string) {
    setKind(next as Kind);
    setValues({});
  }

  /** The server reads a typed local time in the zone of the schedule: the browser never does. */
  async function toEpoch(local: string | undefined): Promise<Outcome<number> | undefined> {
    const text = (local ?? "").trim();
    if (villageId === null || text === "") {
      return undefined;
    }
    const outcome = await settle(() =>
      api().POST("/api/villages/{village_id}/schedule/to-epoch", {
        params: { path: { village_id: villageId } },
        body: { local_datetime: text },
      }),
    );
    return outcome.ok ? { ok: true, data: outcome.data.epoch } : outcome;
  }

  async function build(): Promise<Built> {
    switch (kind) {
      case "start_now": {
        const taskId = get("task").trim();
        return taskId === "" ? undefined : { overlay: { type: kind, task_id: taskId } };
      }
      case "reserve_builder": {
        const count = integerOf(values.count, 1);
        const until = await toEpoch(values.until);
        if (count === undefined || until === undefined) {
          return undefined;
        }
        return until.ok
          ? { overlay: { type: kind, count, until: until.data } }
          : { problem: until.problem };
      }
      case "absence": {
        const start = withSeconds(values.start);
        const end = withSeconds(values.end);
        return start === undefined || end === undefined
          ? undefined
          : { overlay: { type: kind, start_local: start, end_local: end } };
      }
      case "policy": {
        const overlay: components["schemas"]["PolicyIn"] = {
          type: kind,
          mode: (get("mode") || "strict") as Mode,
        };
        for (const [key, field] of [
          ["tolerance_minutes", "tolerance"],
          ["max_logins_per_day", "maxLogins"],
        ] as const) {
          if (get(field).trim() !== "") {
            const value = integerOf(values[field], key === "max_logins_per_day" ? 1 : 0);
            if (value === undefined) {
              return undefined;
            }
            overlay[key] = value;
          }
        }
        return { overlay };
      }
      case "strategy": {
        const key = get("strategy");
        return key === "" ? undefined : { overlay: { type: kind, key } };
      }
      case "reduce_play": {
        const max = integerOf(values.maxLogins, 1);
        return max === undefined ? undefined : { overlay: { type: kind, max_logins_per_day: max } };
      }
      case "use_item": {
        const item = get("item").trim();
        const at = await toEpoch(values.at);
        if (item === "" || at === undefined) {
          return undefined;
        }
        if (!at.ok) {
          return { problem: at.problem };
        }
        const task = get("task").trim();
        return {
          overlay: {
            type: kind,
            item,
            at: at.data,
            ...(task === "" ? {} : { task_id: task }),
          },
        };
      }
    }
  }

  async function submit() {
    setProblem(undefined);
    setLocalError(undefined);
    if (villageId === null) {
      setLocalError(fr.scenarios.needVillage);
      return;
    }
    if (name.trim() === "") {
      setLocalError(fr.scenarios.invalid);
      return;
    }
    setBusy(true);
    const built = await build();
    if (built === undefined) {
      setBusy(false);
      setLocalError(fr.scenarios.invalid);
      return;
    }
    if ("problem" in built) {
      setBusy(false);
      setProblem(built.problem);
      return;
    }
    const outcome = await settle(() =>
      api().POST("/api/villages/{village_id}/scenarios", {
        params: { path: { village_id: villageId } },
        body: { name: name.trim(), overlays: [built.overlay] },
      }),
    );
    setBusy(false);
    if (outcome.ok) {
      setName("");
      setValues({});
      onCreated();
    } else {
      setProblem(outcome.problem);
    }
  }

  return (
    <Section title={fr.scenarios.create}>
      <TextInput label={fr.scenarios.name} value={name} onChange={setName} />
      <SelectInput
        label={fr.scenarios.overlay}
        value={kind}
        onChange={chooseKind}
        options={KINDS.map((key) => [key, fr.scenarios.overlays[key]] as const)}
      />
      {kind === "start_now" && (
        <TextInput label={fr.scenarios.task} value={get("task")} onChange={set("task")} />
      )}
      {kind === "reserve_builder" && (
        <>
          <TextInput
            label={fr.scenarios.builders}
            type="number"
            min={1}
            value={get("count")}
            onChange={set("count")}
          />
          <TextInput
            label={fr.scenarios.until}
            type="datetime-local"
            value={get("until")}
            onChange={set("until")}
          />
        </>
      )}
      {kind === "absence" && (
        <>
          <TextInput
            label={fr.scenarios.absenceStart}
            type="datetime-local"
            value={get("start")}
            onChange={set("start")}
          />
          <TextInput
            label={fr.scenarios.absenceEnd}
            type="datetime-local"
            value={get("end")}
            onChange={set("end")}
          />
        </>
      )}
      {kind === "policy" && (
        <>
          <SelectInput
            label={fr.scenarios.mode}
            value={get("mode") || "strict"}
            onChange={set("mode")}
            options={MODES.map((mode) => [mode, fr.scenarios.modes[mode]] as const)}
          />
          <TextInput
            label={fr.scenarios.tolerance}
            type="number"
            min={0}
            value={get("tolerance")}
            onChange={set("tolerance")}
          />
          <TextInput
            label={fr.scenarios.maxLogins}
            type="number"
            min={1}
            value={get("maxLogins")}
            onChange={set("maxLogins")}
          />
        </>
      )}
      {kind === "strategy" && (
        <SelectInput
          label={fr.scenarios.strategyOf}
          value={get("strategy")}
          onChange={set("strategy")}
          options={[
            ["", fr.scenarios.chooseStrategy],
            ...(strategies.data?.strategies.map((s) => [s.key, s.label] as const) ?? []),
          ]}
        />
      )}
      {kind === "reduce_play" && (
        <TextInput
          label={fr.scenarios.maxLogins}
          type="number"
          min={1}
          value={get("maxLogins")}
          onChange={set("maxLogins")}
        />
      )}
      {kind === "use_item" && (
        <>
          <TextInput label={fr.scenarios.item} value={get("item")} onChange={set("item")} />
          <TextInput label={fr.scenarios.taskOptional} value={get("task")} onChange={set("task")} />
          <TextInput
            label={fr.scenarios.useAt}
            type="datetime-local"
            value={get("at")}
            onChange={set("at")}
          />
        </>
      )}
      <div className="space-y-3">
        <Button onPress={() => void submit()} isDisabled={busy}>
          {fr.scenarios.submit}
        </Button>
        {busy && <p className="text-sm text-mist">{fr.scenarios.creating}</p>}
        {localError !== undefined && <TextAlert>{localError}</TextAlert>}
        <ProblemNotice problem={problem} />
      </div>
    </Section>
  );
}
