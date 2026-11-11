import { useMemo, useState } from "react";
import { Link } from "react-router";
import type { AssignmentOut, PlanOut } from "../api/client";
import { sheetHref, statusLabel } from "../components/gantt/lanes";
import { PlanGate } from "../components/PlanGate";
import { Button } from "../components/ui";
import { fr } from "../i18n/fr";
import { clockFormatter, dayFormatter, formatCivilDay, safeZone, weekOf } from "../lib/time";

function CalendarView({ plan }: { plan: PlanOut }) {
  const timezone = safeZone(plan.timezone);
  const [offset, setOffset] = useState(0);
  const { dayOf, clockOf } = useMemo(
    () => ({ dayOf: dayFormatter(timezone), clockOf: clockFormatter(timezone) }),
    [timezone],
  );
  const endsByDay = useMemo(() => {
    const byDay = new Map<string, AssignmentOut[]>();
    for (const a of [...plan.assignments].sort((x, y) => x.end - y.end)) {
      const day = dayOf(a.end);
      byDay.set(day, [...(byDay.get(day) ?? []), a]);
    }
    return byDay;
  }, [plan.assignments, dayOf]);

  const today = dayOf(Date.now() / 1000);
  const days = weekOf(today, offset);
  const long = { day: "numeric", month: "long", year: "numeric" } as const;
  const title = fr.calendar.week(
    formatCivilDay(days[0] ?? today, long),
    formatCivilDay(days[6] ?? today, long),
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <Button onPress={() => setOffset((o) => o - 1)}>{fr.calendar.previous}</Button>
        <Button onPress={() => setOffset(0)}>{fr.calendar.today}</Button>
        <Button onPress={() => setOffset((o) => o + 1)}>{fr.calendar.next}</Button>
      </div>
      <h3 aria-live="polite" className="font-display text-xl text-bone">
        {title}
      </h3>
      <ol className="grid gap-2 sm:grid-cols-7">
        {days.map((day) => {
          const ends = endsByDay.get(day) ?? [];
          const isToday = day === today;
          return (
            <li
              key={day}
              data-day={day}
              aria-current={isToday ? "date" : undefined}
              className={`min-h-24 rounded-lg border p-2 ${isToday ? "border-signal" : "border-line"} bg-deck`}
            >
              <h4 className="text-xs font-medium tracking-wide text-mist uppercase">
                {formatCivilDay(day, { weekday: "long", day: "numeric", month: "short" })}
              </h4>{" "}
              {ends.length === 0 ? (
                <p className="mt-2 text-xs text-mist/70">{fr.calendar.nothing}</p>
              ) : (
                <ul className="mt-2 space-y-2">
                  {ends.map((a) => (
                    <li key={a.task_id} className="text-sm">
                      <Link
                        to={sheetHref(a.task_id)}
                        className="text-bone underline decoration-line underline-offset-2 hover:decoration-signal"
                      >
                        {a.name}
                      </Link>{" "}
                      <span className="block font-mono text-xs text-mist">
                        {fr.calendar.endsAt} {clockOf(a.end)}
                      </span>{" "}
                      <span className="block text-xs text-mist">{statusLabel(a.status)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

/** Calendrier: the upgrade ends of one week, each in the column of its local day. */
export function Calendar() {
  return <PlanGate title={fr.calendar.title}>{(plan) => <CalendarView plan={plan} />}</PlanGate>;
}
