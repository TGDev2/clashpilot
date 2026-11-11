import { type CSSProperties, useId, useMemo, useState } from "react";
import { Link } from "react-router";
import type { AssignmentOut, SchedulePreview } from "../../api/client";
import { fr } from "../../i18n/fr";
import { formatInZone } from "../../lib/format";
import { formatCivilDay, localDayStarts, SECONDS_PER_DAY } from "../../lib/time";
import { laneRows, sheetHref, statusLabel } from "./lanes";

/** Horizontal room given to one day, so a 90-day plan scrolls instead of squeezing. */
const PX_PER_DAY = 96;
const MIN_WIDTH_PX = 640;
type BandKind = "sleep" | "absence";
const BAND_KINDS: readonly BandKind[] = ["sleep", "absence"];

const percent = (value: number): string => `${value.toFixed(4)}%`;

function Legend() {
  const items = [
    { className: "gantt-frozen", label: fr.plan.status.frozen },
    { className: "gantt-planned", label: fr.plan.status.planned },
    { className: "gantt-sleep", label: fr.gantt.sleep },
    { className: "gantt-absence", label: fr.gantt.absence },
  ];
  return (
    <ul aria-label={fr.gantt.legend} className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-mist">
      {items.map((item) => (
        <li key={item.label} className="flex items-center gap-2">
          <span
            aria-hidden="true"
            className={`inline-block h-3 w-6 rounded-sm ${item.className}`}
          />{" "}
          {item.label}
        </li>
      ))}
    </ul>
  );
}

/**
 * The plan as a hand-drawn Gantt diagram: one row per lane slot, one block per assignment, the
 * sleep and absence bands of the schedule preview behind them. The time axis is linear and covers
 * every assignment; positions are percentages of it. A very short block keeps its left edge at its
 * start and gets a CSS minimum width, so it stays visible without moving.
 */
export function GanttChart({
  assignments,
  timezone,
  preview,
}: {
  assignments: readonly AssignmentOut[];
  timezone: string;
  preview: SchedulePreview | undefined;
}) {
  const baseId = useId();
  const [active, setActive] = useState<string | null>(null);
  const from = Math.min(...assignments.map((a) => a.start));
  const to = Math.max(...assignments.map((a) => a.end));
  const span = Math.max(1, to - from);
  const position = (epoch: number) => ((epoch - from) / span) * 100;
  const rows = useMemo(() => laneRows(assignments), [assignments]);
  const days = useMemo(() => localDayStarts(from, to, timezone), [from, to, timezone]);
  const width = Math.max(MIN_WIDTH_PX, Math.ceil((span / SECONDS_PER_DAY) * PX_PER_DAY));

  const bands = BAND_KINDS.flatMap((kind) =>
    (preview?.[kind] ?? [])
      .flatMap(([start, end]) =>
        start === undefined || end === undefined ? [] : [[start, end] as const],
      )
      .filter(([start, end]) => end > from && start < to)
      .map(([start, end]) => {
        const left = position(Math.max(start, from));
        return { kind, start, end, left, width: position(Math.min(end, to)) - left };
      }),
  );

  const leave = (taskId: string) => setActive((current) => (current === taskId ? null : current));

  return (
    <figure aria-labelledby={`${baseId}-title`} className="space-y-3">
      <figcaption id={`${baseId}-title`} className="font-display text-lg text-bone">
        {fr.gantt.chart}
      </figcaption>
      <Legend />
      <div className="overflow-x-auto rounded-lg border border-line bg-ink pb-28">
        <div className="relative" style={{ minWidth: `${width}px` }}>
          <div aria-hidden="true" className="flex h-8 border-b border-line">
            <div className="sticky left-0 z-10 w-32 shrink-0 bg-ink" />
            <div className="relative flex-1">
              {days.map((day) => (
                <span
                  key={day.at}
                  className="absolute top-1 pl-1 font-mono text-xs whitespace-nowrap text-mist"
                  style={{ left: percent(position(day.at)) }}
                >
                  {formatCivilDay(day.day, { weekday: "short", day: "numeric", month: "short" })}
                </span>
              ))}
            </div>
          </div>
          <div className="relative">
            <div
              aria-hidden="true"
              className="pointer-events-none absolute inset-y-0 right-0 left-32"
            >
              {bands.map((band) => (
                <div
                  key={`${band.kind}-${band.start}`}
                  data-band={band.kind}
                  data-start={band.start}
                  data-end={band.end}
                  className={`absolute inset-y-0 gantt-${band.kind}`}
                  style={{ left: percent(band.left), width: percent(band.width) }}
                />
              ))}
              {days.map((day) => (
                <div
                  key={day.at}
                  className="absolute inset-y-0 border-l border-line/60"
                  style={{ left: percent(position(day.at)) }}
                />
              ))}
            </div>
            {rows.map((row) => (
              <div
                key={row.key}
                data-lane={row.lane}
                data-lane-slot={row.slot}
                className="flex h-12 border-b border-line/60"
              >
                <div className="sticky left-0 z-10 flex w-32 shrink-0 items-center bg-ink px-3 text-sm text-bone">
                  {row.label}
                </div>
                <div className="relative flex-1">
                  {assignments
                    .filter((a) => a.lane === row.lane && a.lane_slot === row.slot)
                    .map((a) => {
                      const left = position(a.start);
                      const tipId = `${baseId}-tip-${a.task_id}`;
                      const shown = active === a.task_id;
                      const start = formatInZone(a.start, timezone);
                      const end = formatInZone(a.end, timezone);
                      const status = statusLabel(a.status);
                      const tipSide: CSSProperties =
                        left < 60 ? { left: percent(left) } : { right: percent(100 - left) };
                      return (
                        <div key={a.task_id}>
                          <Link
                            to={sheetHref(a.task_id)}
                            data-task-id={a.task_id}
                            data-start={a.start}
                            data-end={a.end}
                            data-status={a.status}
                            aria-label={`${a.name}, ${status}, ${fr.plan.fromTo(start, end)}`}
                            aria-describedby={shown ? tipId : undefined}
                            onFocus={() => setActive(a.task_id)}
                            onBlur={() => leave(a.task_id)}
                            onMouseEnter={() => setActive(a.task_id)}
                            onMouseLeave={() => leave(a.task_id)}
                            onKeyDown={(event) => {
                              if (event.key === "Escape") {
                                setActive(null);
                              }
                            }}
                            className={`absolute inset-y-2 z-[1] flex min-w-1.5 items-center overflow-hidden rounded-sm px-1 text-xs text-bone gantt-${a.status}`}
                            style={{ left: percent(left), width: percent(position(a.end) - left) }}
                          >
                            <span aria-hidden="true" className="truncate">
                              {a.name}
                            </span>
                          </Link>
                          {shown && (
                            <div
                              role="tooltip"
                              id={tipId}
                              className="absolute top-full z-20 mt-1 w-max max-w-72 space-y-0.5 rounded-md border border-line bg-deck-hi p-2 text-xs text-bone shadow-lg"
                              style={tipSide}
                            >
                              <p className="font-medium">{a.name}</p>
                              <p className="text-mist">{status}</p>
                              <p>
                                {fr.gantt.startLabel} {start}
                              </p>
                              <p>
                                {fr.gantt.endLabel} {end}
                              </p>
                            </div>
                          )}
                        </div>
                      );
                    })}
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </figure>
  );
}
