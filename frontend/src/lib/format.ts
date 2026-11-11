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
