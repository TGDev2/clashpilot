import { useId, useState } from "react";
import { Section } from "../../components/ui";
import { fr } from "../../i18n/fr";
import {
  loadReminderSettings,
  REMINDER_LANES,
  type ReminderSettings,
  saveReminderSettings,
} from "../../lib/reminders";
import { useVillage } from "../../state/village";

type Permission = "granted" | "denied" | "default" | "unavailable";

function currentPermission(): Permission {
  return typeof Notification === "undefined" ? "unavailable" : Notification.permission;
}

function Checkbox({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  const id = useId();
  return (
    <div className="flex items-center gap-2 text-sm">
      <input
        id={id}
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.currentTarget.checked)}
        className="size-4 accent-signal"
      />
      <label htmlFor={id}>{label}</label>
    </div>
  );
}

/**
 * Reminder settings, saved at once. The reminders only work while the app is open: the page says
 * so, and offers the calendar file for what a phone calendar can ring on its own.
 */
export function NotificationsPanel() {
  const { villageId } = useVillage();
  const [settings, setSettings] = useState<ReminderSettings>(loadReminderSettings);
  const [lead, setLead] = useState(String(settings.leadMinutes));
  const [permission, setPermission] = useState<Permission>(currentPermission);
  const leadId = useId();

  function change(next: ReminderSettings) {
    setSettings(next);
    saveReminderSettings(next);
  }

  function toggleEnabled(enabled: boolean) {
    change({ ...settings, enabled });
    if (enabled && typeof Notification !== "undefined" && Notification.permission === "default") {
      void Promise.resolve(Notification.requestPermission()).then(() =>
        setPermission(currentPermission()),
      );
    }
  }

  function changeLead(text: string) {
    setLead(text);
    const minutes = Number(text);
    if (/^\d+$/.test(text.trim()) && Number.isSafeInteger(minutes) && minutes >= 1) {
      change({ ...settings, leadMinutes: minutes });
    }
  }

  function toggleKind(kind: string, on: boolean) {
    const kinds = REMINDER_LANES.filter((lane) =>
      lane === kind ? on : settings.kinds.includes(lane),
    );
    change({ ...settings, kinds });
  }

  return (
    <Section title={fr.settings.notifications.title}>
      <p className="text-sm text-bone">{fr.settings.notifications.onlyWhenOpen}</p>
      <p className="text-sm text-mist">{fr.settings.notifications.installHint}</p>
      <Checkbox
        label={fr.settings.notifications.enable}
        checked={settings.enabled}
        onChange={toggleEnabled}
      />
      <p className="text-xs text-mist">{fr.settings.notifications.permission[permission]}</p>
      <div className="max-w-xs space-y-1">
        <label htmlFor={leadId} className="block text-xs text-mist">
          {fr.settings.notifications.lead}
        </label>
        <input
          id={leadId}
          type="number"
          min={1}
          inputMode="numeric"
          value={lead}
          onChange={(event) => changeLead(event.currentTarget.value)}
          className="w-full rounded-md border border-line bg-ink px-3 py-2 font-mono text-sm text-bone"
        />
      </div>
      <fieldset className="space-y-2 rounded-lg border border-line/70 p-3">
        <legend className="px-1 text-sm font-medium text-bone">
          {fr.settings.notifications.kinds}
        </legend>
        {REMINDER_LANES.map((lane) => (
          <Checkbox
            key={lane}
            label={fr.lanes[lane]}
            checked={settings.kinds.includes(lane)}
            onChange={(on) => toggleKind(lane, on)}
          />
        ))}
      </fieldset>
      {villageId !== null && (
        <div className="space-y-1">
          <a
            href={`/api/villages/${encodeURIComponent(villageId)}/plans/latest/calendar.ics`}
            className="text-signal underline underline-offset-2"
          >
            {fr.settings.notifications.export}
          </a>
          <p className="text-xs text-mist">{fr.settings.notifications.exportHint}</p>
        </div>
      )}
    </Section>
  );
}
