import { fr } from "../i18n/fr";

const numberFormat = new Intl.NumberFormat("fr-FR");
const dateFormat = new Intl.DateTimeFormat("fr-FR", { dateStyle: "short" });
const dateTimeFormat = new Intl.DateTimeFormat("fr-FR", { dateStyle: "short", timeStyle: "short" });

export function formatNumber(value: number): string {
  return numberFormat.format(value);
}

/** An epoch in seconds as a local date (the server sends UTC epochs, ADR-10). */
export function formatDate(epochSeconds: number): string {
  return dateFormat.format(new Date(epochSeconds * 1000));
}

export function formatDateTime(epochSeconds: number): string {
  return dateTimeFormat.format(new Date(epochSeconds * 1000));
}

/** Time left until `finishAt`, as days, hours and minutes; `null` once it has passed. */
export function formatRemaining(finishAt: number, nowMs: number): string | null {
  const seconds = Math.floor(finishAt - nowMs / 1000);
  if (seconds <= 0) {
    return null;
  }
  const days = Math.floor(seconds / 86_400);
  const hours = Math.floor((seconds % 86_400) / 3_600);
  const minutes = Math.floor((seconds % 3_600) / 60);
  const parts: string[] = [];
  if (days > 0) {
    parts.push(`${days} ${fr.dashboard.days}`);
  }
  if (days > 0 || hours > 0) {
    parts.push(`${hours} ${fr.dashboard.hours}`);
  }
  parts.push(`${minutes} ${fr.dashboard.minutes}`);
  return parts.join(" ");
}

/**
 * An epoch as a wall-clock time in the IANA zone the API sent (ADR-10): `Intl` does the zone
 * arithmetic, the browser's own zone plays no part. An unknown zone falls back to UTC, said aloud.
 */
export function formatInZone(epochSeconds: number, timeZone: string): string {
  const date = new Date(epochSeconds * 1000);
  const options: Intl.DateTimeFormatOptions = { dateStyle: "short", timeStyle: "short" };
  try {
    return new Intl.DateTimeFormat("fr-FR", { ...options, timeZone }).format(date);
  } catch {
    return `${new Intl.DateTimeFormat("fr-FR", { ...options, timeZone: "UTC" }).format(date)} UTC`;
  }
}

const signedFormat = new Intl.NumberFormat("fr-FR", { signDisplay: "exceptZero" });

/** A number with an explicit `+` when positive and `-` when negative; zero stays plain. */
export function formatSigned(value: number): string {
  return signedFormat.format(value);
}

/** A length of time as days, hours and minutes, seconds rounded to the nearest minute. */
export function formatDuration(seconds: number): string {
  const minutesTotal = Math.round(Math.abs(seconds) / 60);
  const days = Math.floor(minutesTotal / 1_440);
  const hours = Math.floor((minutesTotal % 1_440) / 60);
  const minutes = minutesTotal % 60;
  const parts: string[] = [];
  if (days > 0) {
    parts.push(`${days} ${fr.dashboard.days}`);
  }
  if (hours > 0) {
    parts.push(`${hours} ${fr.dashboard.hours}`);
  }
  if (minutes > 0 || parts.length === 0) {
    parts.push(`${minutes} ${fr.dashboard.minutes}`);
  }
  return parts.join(" ");
}

/** The French name of a resource key, the key itself when it has none. */
export function resourceLabel(key: string): string {
  return (fr.resources as Record<string, string>)[key] ?? key;
}
