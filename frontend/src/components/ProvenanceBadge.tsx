import { fr } from "../i18n/fr";

type Status = "confirmed" | "estimated" | "unknown";

const STYLES: Record<Status, { pip: string; frame: string }> = {
  confirmed: { pip: "bg-confirmed", frame: "border-confirmed/60 text-confirmed" },
  estimated: { pip: "bg-estimated", frame: "border-estimated/60 text-estimated" },
  // Unknown is drawn dashed: a gap in the chart, not a low value.
  unknown: { pip: "bg-unknown/50", frame: "border-dashed border-unknown/70 text-unknown" },
};

function isStatus(value: string): value is Status {
  return value === "confirmed" || value === "estimated" || value === "unknown";
}

/** The trust level of a value: Confirmé, Estimé or Inconnu. Anything else reads as Inconnu. */
export function ProvenanceBadge({ status, title }: { status: string; title?: string }) {
  const known = isStatus(status) ? status : "unknown";
  const style = STYLES[known];
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs font-medium tracking-wide ${style.frame}`}
    >
      <span aria-hidden="true" className={`size-1.5 rounded-full ${style.pip}`} />
      {fr.status[known]}
    </span>
  );
}
