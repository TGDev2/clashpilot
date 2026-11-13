import { type ReactNode, useEffect, useRef, useState } from "react";
import { type ApiProblem, api, type ManualOut, settle } from "../../api/client";
import { useApi } from "../../api/useApi";
import { ProvenanceBadge } from "../../components/ProvenanceBadge";
import { Button, inputClass, ProblemAlert, Section, TextAlert } from "../../components/ui";
import { fr } from "../../i18n/fr";
import { formatDate, formatNumber } from "../../lib/format";

const RESOURCES = [
  "gold",
  "elixir",
  "dark_elixir",
  "gems",
  "shiny_ore",
  "glowy_ore",
  "starry_ore",
] as const;
type Resource = (typeof RESOURCES)[number];
type TypedStatus = "confirmed" | "estimated";
type Rate = { low: string; expected: string; high: string };
type ItemDraft = { id: number; name: string; quantity: string };
type EventDraft = {
  id: number;
  label: string;
  lane: string;
  start: string;
  end: string;
  durationCut: string;
  costCut: string;
};
const EVENT_LANES = ["builder", "lab", "pet_house", "blacksmith"] as const;

type Draft = {
  values: Record<Resource, string>;
  statuses: Record<Resource, TypedStatus>;
  rates: Record<Resource, Rate>;
  builders: string;
  items: ItemDraft[];
  events: EventDraft[];
};

function everyResource<T>(make: () => T): Record<Resource, T> {
  return Object.fromEntries(RESOURCES.map((name) => [name, make()])) as Record<Resource, T>;
}

let nextItemId = 1;
function blankItem(): ItemDraft {
  return { id: nextItemId++, name: "", quantity: "" };
}

function blankEvent(): EventDraft {
  return {
    id: nextItemId++,
    label: "",
    lane: "builder",
    start: "",
    end: "",
    durationCut: "",
    costCut: "",
  };
}

/** `datetime-local` text of an epoch, in the browser's time zone. */
function localInput(epoch: number): string {
  const date = new Date(epoch * 1000);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function blankDraft(): Draft {
  return {
    values: everyResource(() => ""),
    statuses: everyResource<TypedStatus>(() => "confirmed"),
    rates: everyResource(() => ({ low: "", expected: "", high: "" })),
    builders: "",
    items: [blankItem()],
    events: [],
  };
}

/** The form as it was last saved: the server's values, never a default. */
function draftOf(saved: ManualOut): Draft {
  const draft = blankDraft();
  for (const name of RESOURCES) {
    const resource = saved.resources[name];
    if (resource) {
      draft.values[name] = String(resource.value);
      draft.statuses[name] = resource.status === "estimated" ? "estimated" : "confirmed";
    }
    const rate = saved.farm_rate[name];
    if (rate) {
      draft.rates[name] = {
        low: String(rate.low),
        expected: String(rate.expected),
        high: String(rate.high),
      };
    }
  }
  draft.builders = saved.builders_total === null ? "" : String(saved.builders_total);
  const items = Object.entries(saved.magic_items).map(([name, quantity]) => ({
    id: nextItemId++,
    name,
    quantity: String(quantity),
  }));
  draft.items = items.length > 0 ? items : [blankItem()];
  draft.events = saved.events.map((event) => ({
    id: nextItemId++,
    label: String(event.label),
    lane: String(event.lane),
    start: localInput(Number(event.start)),
    end: localInput(Number(event.end)),
    durationCut: String(100 - Number(event.duration_pct)),
    costCut: String(100 - Number(event.cost_pct)),
  }));
  return draft;
}

const INTEGER = /^\d+$/;

class DraftError extends Error {}

function count(text: string): number | null {
  const trimmed = text.trim();
  if (trimmed === "") {
    return null;
  }
  if (!INTEGER.test(trimmed)) {
    throw new DraftError(fr.manual.notAnInteger);
  }
  return Number(trimmed);
}

/** Build the request body. A blank field is left out: absent means unknown, never 0. */
function bodyOf(draft: Draft, asOf: number) {
  const resources: Record<string, { value: number; status: TypedStatus; as_of: number }> = {};
  const farmRate: Record<string, { low: number; expected: number; high: number }> = {};
  for (const name of RESOURCES) {
    const value = count(draft.values[name]);
    if (value !== null) {
      resources[name] = { value, status: draft.statuses[name], as_of: asOf };
    }
    const lo = count(draft.rates[name].low);
    const ex = count(draft.rates[name].expected);
    const hi = count(draft.rates[name].high);
    if (lo === null && ex === null && hi === null) {
      continue;
    }
    if (lo === null || ex === null || hi === null) {
      throw new DraftError(`${fr.resources[name]} : ${fr.manual.farmIncomplete}`);
    }
    farmRate[name] = { low: lo, expected: ex, high: hi };
  }
  const magicItems: Record<string, number> = {};
  for (const item of draft.items) {
    const name = item.name.trim();
    const quantity = count(item.quantity);
    if (name === "" && quantity === null) {
      continue;
    }
    if (name === "" || quantity === null) {
      throw new DraftError(fr.manual.itemIncomplete);
    }
    magicItems[name] = quantity;
  }
  const events = draft.events.map((event) => {
    const start = Date.parse(event.start) / 1000;
    const end = Date.parse(event.end) / 1000;
    const durationCut = count(event.durationCut) ?? 0;
    const costCut = count(event.costCut) ?? 0;
    if (event.label.trim() === "" || Number.isNaN(start) || Number.isNaN(end) || end <= start) {
      throw new DraftError(fr.manual.eventIncomplete);
    }
    if (durationCut > 100 || costCut > 100) {
      throw new DraftError(fr.manual.eventPercent);
    }
    return {
      label: event.label.trim(),
      lane: event.lane,
      start,
      end,
      duration_pct: 100 - durationCut,
      cost_pct: 100 - costCut,
    };
  });
  return {
    resources,
    builders_total: count(draft.builders),
    farm_rate: farmRate,
    magic_items: magicItems,
    events,
  };
}

function NumberField({
  label,
  value,
  onChange,
}: {
  label: ReactNode;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block space-y-1">
      <span className="block text-xs text-mist">{label}</span>
      <input
        type="number"
        inputMode="numeric"
        min={0}
        step={1}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={inputClass}
      />
    </label>
  );
}

function shown(value: number | undefined): string {
  return value === undefined ? "—" : formatNumber(value);
}

function Saved({ saved }: { saved: ManualOut }) {
  const resources = RESOURCES.flatMap((name) => {
    const resource = saved.resources[name];
    return resource ? [{ name, resource }] : [];
  });
  return (
    <div className="space-y-3 rounded-lg border border-line bg-ink p-4">
      <h3 className="font-display text-lg">{fr.manual.savedTitle}</h3>
      <ul>
        {resources.map(({ name, resource }) => (
          <li key={name} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1">
            <span className="w-40">{fr.resources[name]}</span>
            <span className="font-mono tabular-nums">{formatNumber(resource.value)}</span>
            <ProvenanceBadge status={resource.status} />
            <span className="text-xs text-mist">{fr.common.asOf(formatDate(resource.as_of))}</span>
          </li>
        ))}
        {saved.builders_total !== null && (
          <li className="flex flex-wrap items-center gap-x-3 py-1">
            <span className="w-40">{fr.dashboard.builders}</span>
            <span className="font-mono tabular-nums">{formatNumber(saved.builders_total)}</span>
          </li>
        )}
        {RESOURCES.flatMap((name) => {
          const rate = saved.farm_rate[name];
          return rate
            ? [
                <li key={`rate-${name}`} className="flex flex-wrap items-center gap-x-3 py-1">
                  <span className="w-40">{`${fr.manual.farmTitle} ${fr.resources[name]}`}</span>
                  <span className="font-mono text-sm tabular-nums">
                    {[rate.low, rate.expected, rate.high].map(shown).join(" / ")}
                  </span>
                </li>,
              ]
            : [];
        })}
        {Object.entries(saved.magic_items).map(([name, quantity]) => (
          <li key={`item-${name}`} className="flex flex-wrap items-center gap-x-3 py-1">
            <span className="w-40 font-mono text-sm">{name}</span>
            <span className="font-mono tabular-nums">{formatNumber(quantity)}</span>
          </li>
        ))}
      </ul>
      {saved.updated_at !== null && (
        <p className="text-xs text-mist">{fr.manual.savedAt(formatDate(saved.updated_at))}</p>
      )}
    </div>
  );
}

/** What the export cannot say: stocks, builders, income per hour, magic items. */
export function ManualForm({ villageId }: { villageId: string | null }) {
  const [draft, setDraft] = useState<Draft>(blankDraft);
  const [saved, setSaved] = useState<ManualOut | undefined>();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<ApiProblem | undefined>();
  const [localError, setLocalError] = useState<string | undefined>();
  const touched = useRef(false);

  const stored = useApi(
    () =>
      settle(() =>
        api().GET("/api/villages/{village_id}/manual", {
          params: { path: { village_id: villageId ?? "" } },
        }),
      ),
    villageId,
  );

  // Show what is already saved, unless the player has started typing.
  useEffect(() => {
    const data = stored.data;
    if (data === undefined || data.updated_at === null) {
      return;
    }
    setSaved(data);
    if (!touched.current) {
      setDraft(draftOf(data));
    }
  }, [stored.data]);

  function edit(change: (current: Draft) => Draft) {
    touched.current = true;
    setDraft(change);
  }

  async function submit() {
    if (villageId === null) {
      return;
    }
    setProblem(undefined);
    setLocalError(undefined);
    let body: ReturnType<typeof bodyOf>;
    try {
      body = bodyOf(draft, Math.floor(Date.now() / 1000));
    } catch (error) {
      if (error instanceof DraftError) {
        setLocalError(error.message);
        return;
      }
      throw error;
    }
    // PUT replaces the whole typed-in state: keep what this form does not show.
    const kept = {
      gold_pass: saved?.gold_pass ?? {},
      storage_override: saved?.storage_override ?? {},
    };
    setBusy(true);
    const outcome = await settle(() =>
      api().PUT("/api/villages/{village_id}/manual", {
        params: { path: { village_id: villageId } },
        body: { ...kept, ...body },
      }),
    );
    setBusy(false);
    if (outcome.ok) {
      setSaved(outcome.data);
    } else {
      setProblem(outcome.problem);
    }
  }

  return (
    <Section id="saisie" title={fr.manual.title}>
      <p className="text-sm text-mist">{fr.manual.hint}</p>
      {villageId === null && <p className="text-sm text-mist">{fr.manual.needVillage}</p>}
      <form
        className="space-y-6"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <div className="space-y-3">
          <h3 className="text-sm font-medium">{fr.manual.resourcesTitle}</h3>
          <div className="grid gap-4 sm:grid-cols-2">
            {RESOURCES.map((name) => (
              <div key={name} className="space-y-2 rounded-lg border border-line/70 p-3">
                <div className="grid grid-cols-[1fr_auto] items-end gap-2">
                  <NumberField
                    label={fr.resources[name]}
                    value={draft.values[name]}
                    onChange={(value) =>
                      edit((d) => ({ ...d, values: { ...d.values, [name]: value } }))
                    }
                  />
                  <label className="block">
                    <span className="sr-only">{fr.manual.status(fr.resources[name])}</span>
                    <select
                      value={draft.statuses[name]}
                      onChange={(event) =>
                        edit((d) => ({
                          ...d,
                          statuses: { ...d.statuses, [name]: event.target.value as TypedStatus },
                        }))
                      }
                      className={`${inputClass} w-auto`}
                    >
                      <option value="confirmed">{fr.status.confirmed}</option>
                      <option value="estimated">{fr.status.estimated}</option>
                    </select>
                  </label>
                </div>
                <p className="text-xs text-mist">{fr.manual.farmTitle}</p>
                <div className="grid grid-cols-3 gap-2">
                  {(
                    [
                      ["low", fr.manual.farmLow],
                      ["expected", fr.manual.farmExpected],
                      ["high", fr.manual.farmHigh],
                    ] as const
                  ).map(([field, caption]) => (
                    <NumberField
                      key={field}
                      label={
                        <>
                          <span className="sr-only">{fr.manual.farmPrefix}</span>
                          {caption}
                          <span className="sr-only">{` ${fr.resources[name]}`}</span>
                        </>
                      }
                      value={draft.rates[name][field]}
                      onChange={(value) =>
                        edit((d) => ({
                          ...d,
                          rates: { ...d.rates, [name]: { ...d.rates[name], [field]: value } },
                        }))
                      }
                    />
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="max-w-xs">
          <NumberField
            label={fr.manual.builders}
            value={draft.builders}
            onChange={(value) => edit((d) => ({ ...d, builders: value }))}
          />
        </div>

        <div className="space-y-3">
          <h3 className="text-sm font-medium">{fr.manual.magicItems}</h3>
          {draft.items.map((item) => (
            <div key={item.id} className="grid max-w-lg grid-cols-[2fr_1fr] gap-2">
              <label className="block space-y-1">
                <span className="block text-xs text-mist">{fr.manual.itemName}</span>
                <input
                  type="text"
                  value={item.name}
                  onChange={(event) =>
                    edit((d) => ({
                      ...d,
                      items: d.items.map((i) =>
                        i.id === item.id ? { ...i, name: event.target.value } : i,
                      ),
                    }))
                  }
                  className={inputClass}
                />
              </label>
              <NumberField
                label={fr.manual.quantity}
                value={item.quantity}
                onChange={(value) =>
                  edit((d) => ({
                    ...d,
                    items: d.items.map((i) => (i.id === item.id ? { ...i, quantity: value } : i)),
                  }))
                }
              />
            </div>
          ))}
          <Button onPress={() => edit((d) => ({ ...d, items: [...d.items, blankItem()] }))}>
            {fr.manual.addItem}
          </Button>
        </div>

        <div className="space-y-3">
          <h3 className="text-sm font-medium">{fr.manual.eventsTitle}</h3>
          <p className="text-xs text-mist">{fr.manual.eventsHint}</p>
          {draft.events.map((event) => {
            const change = (patch: Partial<EventDraft>) =>
              edit((d) => ({
                ...d,
                events: d.events.map((e) => (e.id === event.id ? { ...e, ...patch } : e)),
              }));
            return (
              <div
                key={event.id}
                className="grid gap-2 rounded-lg border border-line/70 p-3 sm:grid-cols-3"
              >
                <label className="block space-y-1">
                  <span className="block text-xs text-mist">{fr.manual.eventLabel}</span>
                  <input
                    type="text"
                    value={event.label}
                    onChange={(e) => change({ label: e.target.value })}
                    className={inputClass}
                  />
                </label>
                <label className="block space-y-1">
                  <span className="block text-xs text-mist">{fr.manual.eventLane}</span>
                  <select
                    value={event.lane}
                    onChange={(e) => change({ lane: e.target.value })}
                    className={inputClass}
                  >
                    {EVENT_LANES.map((lane) => (
                      <option key={lane} value={lane}>
                        {fr.manual.lanes[lane]}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block space-y-1">
                  <span className="block text-xs text-mist">{fr.manual.eventStart}</span>
                  <input
                    type="datetime-local"
                    value={event.start}
                    onChange={(e) => change({ start: e.target.value })}
                    className={inputClass}
                  />
                </label>
                <label className="block space-y-1">
                  <span className="block text-xs text-mist">{fr.manual.eventEnd}</span>
                  <input
                    type="datetime-local"
                    value={event.end}
                    onChange={(e) => change({ end: e.target.value })}
                    className={inputClass}
                  />
                </label>
                <NumberField
                  label={fr.manual.eventDurationCut}
                  value={event.durationCut}
                  onChange={(value) => change({ durationCut: value })}
                />
                <NumberField
                  label={fr.manual.eventCostCut}
                  value={event.costCut}
                  onChange={(value) => change({ costCut: value })}
                />
                <Button
                  onPress={() =>
                    edit((d) => ({ ...d, events: d.events.filter((e) => e.id !== event.id) }))
                  }
                >
                  {fr.manual.removeEvent}
                </Button>
              </div>
            );
          })}
          <Button onPress={() => edit((d) => ({ ...d, events: [...d.events, blankEvent()] }))}>
            {fr.manual.addEvent}
          </Button>
        </div>

        <Button type="submit" isDisabled={villageId === null || busy}>
          {busy ? fr.manual.saving : fr.manual.submit}
        </Button>
      </form>
      {localError && <TextAlert>{localError}</TextAlert>}
      <ProblemAlert problem={problem} />
      {saved && <Saved saved={saved} />}
    </Section>
  );
}
