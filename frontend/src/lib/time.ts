/**
 * Local dates for display (ADR-10). The server sends UTC epochs and the plan's IANA zone; every
 * epoch -> local date conversion goes through `Intl.DateTimeFormat` with that zone, never through
 * the browser's own zone. Local days are civil dates written `YYYY-MM-DD`; stepping from one civil
 * date to the next is plain calendar arithmetic and involves no zone.
 */

const DAY_S = 86_400;
const QUARTER_HOUR_S = 900;
const HOUR_S = 3_600;

/** The zone itself when `Intl` knows it, else UTC (the API validates zones, this is a safety net). */
export function safeZone(timeZone: string): string {
  try {
    new Intl.DateTimeFormat("fr-FR", { timeZone });
    return timeZone;
  } catch {
    return "UTC";
  }
}

/** A function giving the local civil date (`YYYY-MM-DD`) of an epoch in `timeZone`. */
export function dayFormatter(timeZone: string): (epochSeconds: number) => string {
  const format = new Intl.DateTimeFormat("fr-FR", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });
  return (epochSeconds) => {
    const parts = format.formatToParts(new Date(epochSeconds * 1000));
    const part = (type: Intl.DateTimeFormatPartTypes) =>
      parts.find((p) => p.type === type)?.value ?? "";
    return `${part("year")}-${part("month")}-${part("day")}`;
  };
}

/** A function giving the local wall-clock time (`HH:MM`) of an epoch in `timeZone`. */
export function clockFormatter(timeZone: string): (epochSeconds: number) => string {
  const format = new Intl.DateTimeFormat("fr-FR", {
    timeZone,
    hour: "2-digit",
    minute: "2-digit",
  });
  return (epochSeconds) => format.format(new Date(epochSeconds * 1000));
}

function civilParts(day: string): [number, number, number] {
  const [year = 1970, month = 1, date = 1] = day.split("-").map(Number);
  return [year, month, date];
}

/** The civil date `count` days after `day` (negative goes back). */
export function addDays(day: string, count: number): string {
  const [year, month, date] = civilParts(day);
  return new Date(Date.UTC(year, month - 1, date + count)).toISOString().slice(0, 10);
}

/** The seven civil dates, Monday to Sunday, of the week holding `day`, moved by `offsetWeeks`. */
export function weekOf(day: string, offsetWeeks = 0): string[] {
  const [year, month, date] = civilParts(day);
  const fromMonday = (new Date(Date.UTC(year, month - 1, date)).getUTCDay() + 6) % 7;
  const monday = addDays(day, 7 * offsetWeeks - fromMonday);
  return Array.from({ length: 7 }, (_, index) => addDays(monday, index));
}

/** A civil date written in French, e.g. "lun. 9 nov." (the date has no zone: formatted as UTC). */
export function formatCivilDay(day: string, options: Intl.DateTimeFormatOptions): string {
  const [year, month, date] = civilParts(day);
  return new Intl.DateTimeFormat("fr-FR", { ...options, timeZone: "UTC" }).format(
    new Date(Date.UTC(year, month - 1, date)),
  );
}

export type LocalDayStart = { day: string; at: number };

/**
 * The local midnights in `[from, to]`: each instant where the local date in `timeZone` changes.
 * Scanned hour by hour, then refined to the quarter hour (every offset in use is a multiple of 15
 * minutes), so daylight saving days of 23 or 25 hours fall where the zone puts them.
 */
export function localDayStarts(from: number, to: number, timeZone: string): LocalDayStart[] {
  const dayOf = dayFormatter(timeZone);
  const starts: LocalDayStart[] = [];
  let previousAt = Math.floor(from / HOUR_S) * HOUR_S;
  let previousDay = dayOf(previousAt);
  for (let at = previousAt + HOUR_S; at <= to + HOUR_S; at += HOUR_S) {
    const day = dayOf(at);
    if (day !== previousDay) {
      let start = at;
      for (let q = previousAt + QUARTER_HOUR_S; q < at; q += QUARTER_HOUR_S) {
        if (dayOf(q) !== previousDay) {
          start = q;
          break;
        }
      }
      if (start >= from && start <= to) {
        starts.push({ day: dayOf(start), at: start });
      }
      previousDay = day;
    }
    previousAt = at;
  }
  return starts;
}

export const SECONDS_PER_DAY = DAY_S;
