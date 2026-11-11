import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useLocation } from "react-router";
import { useLatestPlan } from "../api/usePlan";
import { fr } from "../i18n/fr";
import {
  parseReminderSettings,
  type Reminder,
  rawReminderSettings,
  scheduleReminders,
  subscribeReminderSettings,
} from "../lib/reminders";
import { useVillage } from "../state/village";
import { Button } from "./ui";

/** Ends further away than this are left for a later visit: a page stays open for hours, not weeks. */
const RANGE_DAYS = 30;

/**
 * Schedules the reminders of the latest plan while the app is open, and announces in the page
 * those that did not become a system notification. The plan is read again on each navigation, so
 * that a replanning moves the reminders; an end already announced is not announced twice.
 */
export function ReminderAnnouncer() {
  const { villageId } = useVillage();
  const { pathname } = useLocation();
  const raw = useSyncExternalStore(subscribeReminderSettings, rawReminderSettings);
  const settings = useMemo(() => parseReminderSettings(raw), [raw]);
  // Nothing is read from the server while the reminders are off.
  const plan = useLatestPlan(settings.enabled ? villageId : null, pathname).data;
  const [shown, setShown] = useState<Reminder[]>([]);
  const reminded = useRef(new Set<string>());

  useEffect(() => {
    if (plan === undefined) {
      return;
    }
    const now = Math.floor(Date.now() / 1000);
    return scheduleReminders(
      plan.assignments,
      settings,
      { from: now, to: now + RANGE_DAYS * 86_400 },
      (reminder) => setShown((current) => [...current, reminder]),
      reminded.current,
    );
  }, [plan, settings]);

  return (
    <div role="status" aria-label={fr.reminders.heading} className="mx-auto w-full max-w-5xl px-4">
      {shown.length > 0 && (
        <ul className="mt-3 space-y-2">
          {shown.map((reminder) => (
            <li
              key={`${reminder.taskId}:${reminder.end}`}
              className="flex items-center justify-between gap-3 rounded-md border border-estimated bg-deck px-3 py-2 text-sm"
            >
              <span>{reminder.message}</span>
              <Button
                onPress={() =>
                  setShown((current) =>
                    current.filter(
                      (item) => item.taskId !== reminder.taskId || item.end !== reminder.end,
                    ),
                  )
                }
              >
                {fr.reminders.dismiss}
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
