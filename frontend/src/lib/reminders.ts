/**
 * In-app reminders of the ends of a plan (E21). They live in the open page: a `setTimeout` per
 * end, no push and nothing in the background. A system notification is used only when the
 * browser grants it, otherwise the reminder goes to the page.
 */
import { fr } from "../i18n/fr";

export type ReminderSettings = { enabled: boolean; leadMinutes: number; kinds: string[] };

export type Reminder = { taskId: string; name: string; lane: string; end: number; message: string };

/** The part of a plan assignment a reminder reads. */
export type ReminderAssignment = { task_id: string; name: string; lane: string; end: number };

export const REMINDER_LANES = ["builder", "lab", "pet_house", "blacksmith"] as const;

export const DEFAULT_REMINDER_SETTINGS: ReminderSettings = {
  enabled: false,
  leadMinutes: 10,
  kinds: [...REMINDER_LANES],
};

export const REMINDERS_STORAGE_KEY = "clashpilot.reminders";

/** Browsers refuse a timer longer than this many milliseconds (a signed 32-bit integer). */
export const MAX_TIMER_MS = 2 ** 31 - 1;

const listeners = new Set<() => void>();

function defaults(): ReminderSettings {
  return { ...DEFAULT_REMINDER_SETTINGS, kinds: [...DEFAULT_REMINDER_SETTINGS.kinds] };
}

export function parseReminderSettings(raw: string | null): ReminderSettings {
  if (raw === null) {
    return defaults();
  }
  try {
    const value: unknown = JSON.parse(raw);
    if (typeof value !== "object" || value === null) {
      return defaults();
    }
    const { enabled, leadMinutes, kinds } = value as Record<string, unknown>;
    if (
      typeof enabled !== "boolean" ||
      typeof leadMinutes !== "number" ||
      !Number.isFinite(leadMinutes) ||
      leadMinutes < 0 ||
      !Array.isArray(kinds) ||
      !kinds.every((kind) => typeof kind === "string")
    ) {
      return defaults();
    }
    return { enabled, leadMinutes, kinds: [...(kinds as string[])] };
  } catch {
    return defaults();
  }
}

export function rawReminderSettings(): string | null {
  try {
    return localStorage.getItem(REMINDERS_STORAGE_KEY);
  } catch {
    return null;
  }
}

export function loadReminderSettings(): ReminderSettings {
  return parseReminderSettings(rawReminderSettings());
}

export function saveReminderSettings(settings: ReminderSettings): void {
  try {
    localStorage.setItem(REMINDERS_STORAGE_KEY, JSON.stringify(settings));
  } catch {
    // Private mode: the settings then live for this page only.
  }
  for (const listener of listeners) {
    listener();
  }
}

/** Call `listener` whenever the settings change (here or in another tab). Returns the unsubscribe. */
export function subscribeReminderSettings(listener: () => void): () => void {
  listeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    if (event.key === REMINDERS_STORAGE_KEY || event.key === null) {
      listener();
    }
  };
  window.addEventListener("storage", onStorage);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

function systemNotificationAllowed(): boolean {
  return typeof Notification !== "undefined" && Notification.permission === "granted";
}

/**
 * Plan a reminder `settings.leadMinutes` before every end in `range` (UTC epochs in seconds) of
 * the lanes in `settings.kinds`. An end already past gives nothing; a reminder whose time has
 * passed but whose end has not fires at once. `onInApp` receives the reminders that did not
 * become a system notification. `reminded` (optional) keeps the ends already announced, so that
 * scheduling again does not announce them twice. Returns the function that cancels every timer.
 */
export function scheduleReminders(
  assignments: readonly ReminderAssignment[],
  settings: ReminderSettings,
  range: { from: number; to: number },
  onInApp: (reminder: Reminder) => void,
  reminded: Set<string> = new Set(),
): () => void {
  const timers = new Set<ReturnType<typeof setTimeout>>();
  let cancelled = false;
  if (!settings.enabled) {
    return () => undefined;
  }

  /** Run `action` at the epoch `target` (ms), through timers of at most MAX_TIMER_MS each. */
  const wait = (target: number, action: () => void) => {
    const arm = () => {
      const remaining = target - Date.now();
      const handle = setTimeout(
        () => {
          timers.delete(handle);
          if (cancelled) {
            return;
          }
          if (target - Date.now() > 0) {
            arm();
          } else {
            action();
          }
        },
        Math.min(Math.max(remaining, 0), MAX_TIMER_MS),
      );
      timers.add(handle);
    };
    arm();
  };

  const fire = (assignment: ReminderAssignment, key: string) => {
    if (reminded.has(key)) {
      return;
    }
    reminded.add(key);
    const minutes = Math.max(0, Math.round((assignment.end * 1000 - Date.now()) / 60_000));
    const reminder: Reminder = {
      taskId: assignment.task_id,
      name: assignment.name,
      lane: assignment.lane,
      end: assignment.end,
      message: fr.reminders.message(assignment.name, minutes),
    };
    if (systemNotificationAllowed()) {
      try {
        new Notification(fr.reminders.title, { body: reminder.message, tag: key });
        return;
      } catch {
        // Some browsers refuse `new Notification` (Android): the page announces it instead.
      }
    }
    onInApp(reminder);
  };

  for (const assignment of assignments) {
    const key = `${assignment.task_id}:${assignment.end}`;
    const wanted =
      settings.kinds.includes(assignment.lane) &&
      assignment.end >= range.from &&
      assignment.end <= range.to &&
      assignment.end * 1000 > Date.now() &&
      !reminded.has(key);
    if (wanted) {
      wait((assignment.end - settings.leadMinutes * 60) * 1000, () => fire(assignment, key));
    }
  }

  return () => {
    cancelled = true;
    for (const handle of timers) {
      clearTimeout(handle);
    }
    timers.clear();
  };
}
