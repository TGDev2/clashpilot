import { memo, useCallback, useEffect, useRef, useState } from "react";
import { Link, Radio, RadioGroup, Label as RadioLabel } from "react-aria-components";
import {
  type ApiProblem,
  api,
  type ScheduleProfile,
  settle,
  type WeeklyWindow,
} from "../api/client";
import { useApi } from "../api/useApi";
import {
  Button,
  Field,
  inputClass,
  PageTitle,
  ProblemAlert,
  Section,
  TextAlert,
} from "../components/ui";
import { fr } from "../i18n/fr";
import { useVillage } from "../state/village";
import { NotificationsPanel } from "./settings/NotificationsPanel";

type Kind = WeeklyWindow["kind"];
type Mode = ScheduleProfile["policy"]["mode"];

const KINDS: readonly Kind[] = ["sleep", "work", "available", "preferred"];
const MODES: readonly Mode[] = ["strict", "tolerance", "progress", "min_logins"];

type WindowDraft = { id: number; kind: Kind; weekday: string; start: string; end: string };
type AbsenceDraft = { id: number; start: string; end: string };
type Draft = {
  tz: string;
  windows: WindowDraft[];
  absences: AbsenceDraft[];
  mode: Mode;
  tolerance: string;
  maxLogins: string;
};

let nextId = 1;

function blankDraft(): Draft {
  return { tz: "", windows: [], absences: [], mode: "strict", tolerance: "", maxLogins: "" };
}

/** The form as the server stored it. A weekday is "" for every day, else "0" (Monday) to "6". */
function draftOf(profile: ScheduleProfile): Draft {
  return {
    tz: profile.tz,
    windows: profile.weekly.map((w) => ({
      id: nextId++,
      kind: w.kind,
      weekday: w.weekday === null ? "" : String(w.weekday),
      start: w.start,
      end: w.end,
    })),
    absences: profile.exceptions.map((e) => ({
      id: nextId++,
      start: e.start_local,
      end: e.end_local,
    })),
    mode: profile.policy.mode,
    tolerance: profile.policy.mode === "tolerance" ? String(profile.policy.tolerance_minutes) : "",
    maxLogins:
      profile.policy.mode === "min_logins" && profile.policy.max_logins_per_day !== null
        ? String(profile.policy.max_logins_per_day)
        : "",
  };
}

const CLOCK = /^([01]\d|2[0-3]):[0-5]\d$/;
const LOCAL = /^\d{4}-\d{2}-\d{2}T([01]\d|2[0-3]):[0-5]\d:[0-5]\d$/;
const COUNT = /^\d+$/;

type Errors = Record<string, string>;

/** The request body, or the errors by field key. The server stays the judge of the values. */
function bodyOf(draft: Draft): { body: ScheduleProfile } | { errors: Errors } {
  const errors: Errors = {};
  const tz = draft.tz.trim();
  if (tz === "") {
    errors.tz = fr.settings.timezoneRequired;
  }
  for (const w of draft.windows) {
    if (!CLOCK.test(w.start.trim())) {
      errors[`w${w.id}-start`] = fr.settings.invalidClock;
    }
    if (!CLOCK.test(w.end.trim())) {
      errors[`w${w.id}-end`] = fr.settings.invalidClock;
    }
  }
  for (const a of draft.absences) {
    if (!LOCAL.test(a.start.trim())) {
      errors[`a${a.id}-start`] = fr.settings.invalidLocal;
    }
    if (!LOCAL.test(a.end.trim())) {
      errors[`a${a.id}-end`] = fr.settings.invalidLocal;
    }
  }
  let tolerance = 0;
  let maxLogins: number | null = null;
  if (draft.mode === "tolerance") {
    if (COUNT.test(draft.tolerance.trim())) {
      tolerance = Number(draft.tolerance.trim());
    } else {
      errors.tolerance = fr.settings.invalidCount;
    }
  }
  if (draft.mode === "min_logins") {
    if (COUNT.test(draft.maxLogins.trim()) && Number(draft.maxLogins.trim()) >= 1) {
      maxLogins = Number(draft.maxLogins.trim());
    } else {
      errors.maxLogins = fr.settings.invalidCount;
    }
  }
  if (Object.keys(errors).length > 0) {
    return { errors };
  }
  return {
    body: {
      tz,
      weekly: KINDS.flatMap((kind) =>
        draft.windows
          .filter((w) => w.kind === kind)
          .map((w) => ({
            weekday: w.weekday === "" ? null : Number(w.weekday),
            start: w.start.trim(),
            end: w.end.trim(),
            kind,
          })),
      ),
      exceptions: draft.absences.map((a) => ({
        start_local: a.start.trim(),
        end_local: a.end.trim(),
        kind: "absence" as const,
      })),
      policy: { mode: draft.mode, tolerance_minutes: tolerance, max_logins_per_day: maxLogins },
    },
  };
}

function RemoveButton({ onPress }: { onPress: () => void }) {
  return <Button onPress={onPress}>{fr.settings.remove}</Button>;
}

/** One row, memoised: a keystroke re-renders the edited row only, not the whole form. */
const WindowRow = memo(function WindowRow({
  window: w,
  startError,
  endError,
  onChange,
  onRemove,
}: {
  window: WindowDraft;
  startError: string | undefined;
  endError: string | undefined;
  onChange: (id: number, change: Partial<WindowDraft>) => void;
  onRemove: (id: number) => void;
}) {
  return (
    <div className="grid grid-cols-2 items-start gap-3 sm:grid-cols-[1fr_1fr_1fr_auto]">
      <label className="block space-y-1">
        <span className="block text-xs text-mist">{fr.settings.weekday}</span>
        <select
          value={w.weekday}
          onChange={(event) => onChange(w.id, { weekday: event.target.value })}
          className={inputClass}
        >
          <option value="">{fr.settings.everyDay}</option>
          {fr.settings.days.map((day, index) => (
            <option key={day} value={String(index)}>
              {day}
            </option>
          ))}
        </select>
      </label>
      <Field
        label={fr.settings.start}
        value={w.start}
        onChange={(start) => onChange(w.id, { start })}
        placeholder={fr.settings.clockHint}
        error={startError}
      />
      <Field
        label={fr.settings.end}
        value={w.end}
        onChange={(end) => onChange(w.id, { end })}
        placeholder={fr.settings.clockHint}
        error={endError}
      />
      <div className="pt-5">
        <RemoveButton onPress={() => onRemove(w.id)} />
      </div>
    </div>
  );
});

const AbsenceRow = memo(function AbsenceRow({
  absence: a,
  startError,
  endError,
  onChange,
  onRemove,
}: {
  absence: AbsenceDraft;
  startError: string | undefined;
  endError: string | undefined;
  onChange: (id: number, change: Partial<AbsenceDraft>) => void;
  onRemove: (id: number) => void;
}) {
  return (
    <div className="grid items-start gap-3 sm:grid-cols-[1fr_1fr_auto]">
      <Field
        label={fr.settings.absenceStart}
        value={a.start}
        onChange={(start) => onChange(a.id, { start })}
        placeholder={fr.settings.localHint}
        error={startError}
      />
      <Field
        label={fr.settings.absenceEnd}
        value={a.end}
        onChange={(end) => onChange(a.id, { end })}
        placeholder={fr.settings.localHint}
        error={endError}
      />
      <div className="pt-5">
        <RemoveButton onPress={() => onRemove(a.id)} />
      </div>
    </div>
  );
});

function WindowGroup({
  kind,
  windows,
  errors,
  onAdd,
  onChange,
  onRemove,
}: {
  kind: Kind;
  windows: WindowDraft[];
  errors: Errors;
  onAdd: (kind: Kind) => void;
  onChange: (id: number, change: Partial<WindowDraft>) => void;
  onRemove: (id: number) => void;
}) {
  return (
    <fieldset className="space-y-3 rounded-lg border border-line/70 p-3">
      <legend className="px-1 text-sm font-medium text-bone">{fr.settings.kinds[kind]}</legend>
      {windows.map((w) => (
        <WindowRow
          key={w.id}
          window={w}
          startError={errors[`w${w.id}-start`]}
          endError={errors[`w${w.id}-end`]}
          onChange={onChange}
          onRemove={onRemove}
        />
      ))}
      <Button onPress={() => onAdd(kind)}>{fr.settings.add}</Button>
    </fieldset>
  );
}

export function Settings() {
  const { villageId } = useVillage();
  const stored = useApi(
    () =>
      settle(() =>
        api().GET("/api/villages/{village_id}/schedule", {
          params: { path: { village_id: villageId ?? "" } },
        }),
      ),
    villageId,
  );
  const [draft, setDraft] = useState<Draft>(blankDraft);
  const [errors, setErrors] = useState<Errors>({});
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [problem, setProblem] = useState<ApiProblem | undefined>();
  const touched = useRef(false);

  // Show what is stored, unless the player has started typing.
  useEffect(() => {
    if (stored.data !== undefined && !touched.current) {
      setDraft(draftOf(stored.data));
    }
  }, [stored.data]);

  const edit = useCallback((change: (current: Draft) => Draft) => {
    touched.current = true;
    setSaved(false);
    setDraft(change);
  }, []);

  const changeWindow = useCallback(
    (id: number, change: Partial<WindowDraft>) =>
      edit((d) => ({
        ...d,
        windows: d.windows.map((w) => (w.id === id ? { ...w, ...change } : w)),
      })),
    [edit],
  );
  const removeWindow = useCallback(
    (id: number) => edit((d) => ({ ...d, windows: d.windows.filter((w) => w.id !== id) })),
    [edit],
  );
  const addWindow = useCallback(
    (kind: Kind) =>
      edit((d) => ({
        ...d,
        windows: [...d.windows, { id: nextId++, kind, weekday: "", start: "", end: "" }],
      })),
    [edit],
  );
  const changeAbsence = useCallback(
    (id: number, change: Partial<AbsenceDraft>) =>
      edit((d) => ({
        ...d,
        absences: d.absences.map((x) => (x.id === id ? { ...x, ...change } : x)),
      })),
    [edit],
  );
  const removeAbsence = useCallback(
    (id: number) => edit((d) => ({ ...d, absences: d.absences.filter((x) => x.id !== id) })),
    [edit],
  );

  async function submit() {
    if (villageId === null) {
      return;
    }
    setProblem(undefined);
    setSaved(false);
    const built = bodyOf(draft);
    if ("errors" in built) {
      setErrors(built.errors);
      return;
    }
    setErrors({});
    setBusy(true);
    const outcome = await settle(() =>
      api().PUT("/api/villages/{village_id}/schedule", {
        params: { path: { village_id: villageId } },
        body: built.body,
      }),
    );
    setBusy(false);
    if (outcome.ok) {
      setSaved(true);
    } else {
      setProblem(outcome.problem);
    }
  }

  const loadProblem = stored.problem?.code === "schedule_not_found" ? undefined : stored.problem;

  return (
    <>
      <PageTitle>{fr.settings.title}</PageTitle>
      <div className="space-y-6">
        <p className="text-sm text-mist">{fr.settings.hint}</p>
        {villageId === null && (
          <p className="text-mist">
            {fr.now.needVillage}{" "}
            <Link href="/importer" className="text-signal underline underline-offset-2">
              {fr.dashboard.noVillageLink}
            </Link>
          </p>
        )}
        <ProblemAlert problem={loadProblem} />
        <form
          className="space-y-6"
          onSubmit={(event) => {
            event.preventDefault();
            void submit();
          }}
        >
          <Section title={fr.settings.clockTitle}>
            <div className="max-w-sm">
              <Field
                label={fr.settings.timezone}
                value={draft.tz}
                onChange={(tz) => edit((d) => ({ ...d, tz }))}
                hint={fr.settings.timezoneHint}
                placeholder="Europe/Paris"
                error={errors.tz}
              />
            </div>
          </Section>

          <Section title={fr.settings.windowsTitle}>
            {KINDS.map((kind) => (
              <WindowGroup
                key={kind}
                kind={kind}
                windows={draft.windows.filter((w) => w.kind === kind)}
                errors={errors}
                onAdd={addWindow}
                onChange={changeWindow}
                onRemove={removeWindow}
              />
            ))}
            <fieldset className="space-y-3 rounded-lg border border-line/70 p-3">
              <legend className="px-1 text-sm font-medium text-bone">{fr.settings.absences}</legend>
              {draft.absences.map((a) => (
                <AbsenceRow
                  key={a.id}
                  absence={a}
                  startError={errors[`a${a.id}-start`]}
                  endError={errors[`a${a.id}-end`]}
                  onChange={changeAbsence}
                  onRemove={removeAbsence}
                />
              ))}
              <Button
                onPress={() =>
                  edit((d) => ({
                    ...d,
                    absences: [...d.absences, { id: nextId++, start: "", end: "" }],
                  }))
                }
              >
                {fr.settings.addAbsence}
              </Button>
            </fieldset>
          </Section>

          <Section title={fr.settings.policyTitle}>
            <RadioGroup
              value={draft.mode}
              onChange={(mode) => edit((d) => ({ ...d, mode: mode as Mode }))}
              className="space-y-2"
            >
              <RadioLabel className="sr-only">{fr.settings.policy}</RadioLabel>
              {MODES.map((mode) => (
                <Radio key={mode} value={mode} className="flex items-center gap-2 text-sm">
                  <span aria-hidden="true" className="size-3 rounded-full border border-signal" />
                  {fr.settings.modes[mode]}
                </Radio>
              ))}
            </RadioGroup>
            {draft.mode === "tolerance" && (
              <div className="max-w-xs">
                <Field
                  label={fr.settings.tolerance}
                  value={draft.tolerance}
                  onChange={(tolerance) => edit((d) => ({ ...d, tolerance }))}
                  inputMode="numeric"
                  error={errors.tolerance}
                />
              </div>
            )}
            {draft.mode === "min_logins" && (
              <div className="max-w-xs">
                <Field
                  label={fr.settings.maxLogins}
                  value={draft.maxLogins}
                  onChange={(maxLogins) => edit((d) => ({ ...d, maxLogins }))}
                  inputMode="numeric"
                  error={errors.maxLogins}
                />
              </div>
            )}
          </Section>

          {Object.keys(errors).length > 0 && <TextAlert>{fr.settings.fixErrors}</TextAlert>}
          <ProblemAlert problem={problem} />
          <div className="flex items-center gap-4">
            <Button type="submit" isDisabled={villageId === null || busy}>
              {busy ? fr.settings.saving : fr.settings.save}
            </Button>
            <p role="status" className="text-sm text-confirmed">
              {saved ? fr.settings.saved : ""}
            </p>
          </div>
        </form>
        <NotificationsPanel />
      </div>
    </>
  );
}
