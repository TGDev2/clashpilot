import type { ReactNode } from "react";
import {
  Button as AriaButton,
  type ButtonProps,
  FieldError,
  Input,
  Label,
  Text,
  TextField,
} from "react-aria-components";
import type { ApiProblem } from "../api/client";

/** A page section: a titled card, optionally the target of an in-page anchor. */
export function Section({
  id,
  title,
  children,
}: {
  id?: string;
  title: string;
  children: ReactNode;
}) {
  const headingId = `${id ?? title}-title`.replace(/\s+/g, "-").toLowerCase();
  return (
    <section
      id={id}
      aria-labelledby={headingId}
      className="scroll-mt-6 rounded-xl border border-line bg-deck p-4 sm:p-6"
    >
      <h2 id={headingId} className="mb-4 font-display text-xl text-bone">
        {title}
      </h2>
      <div className="space-y-4">{children}</div>
    </section>
  );
}

export function PageTitle({ children }: { children: ReactNode }) {
  return <h2 className="mb-6 font-display text-3xl tracking-tight text-bone">{children}</h2>;
}

export function Button({ className = "", ...props }: ButtonProps & { className?: string }) {
  return (
    <AriaButton
      {...props}
      className={`rounded-lg border border-signal/60 bg-signal/10 px-4 py-2 text-sm font-medium text-signal transition-colors motion-reduce:transition-none hover:bg-signal/20 pressed:bg-signal/30 disabled:cursor-not-allowed disabled:border-line disabled:bg-transparent disabled:text-mist ${className}`}
    />
  );
}

/** An API failure, announced to assistive technology; lists `errors` when the API sent some. */
export function ProblemAlert({ problem }: { problem: ApiProblem | undefined | null }) {
  if (!problem) {
    return null;
  }
  return (
    <div role="alert" className="rounded-lg border border-unknown/60 bg-unknown/10 p-3 text-sm">
      <p>{problem.message}</p>
      {problem.errors.length > 0 && (
        <ul className="mt-2 list-disc space-y-1 pl-5 font-mono text-xs">
          {problem.errors.map((error) => (
            <li key={error}>{error}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function TextAlert({ children }: { children: ReactNode }) {
  return (
    <div role="alert" className="rounded-lg border border-unknown/60 bg-unknown/10 p-3 text-sm">
      {children}
    </div>
  );
}

export function Banner({ children }: { children: ReactNode }) {
  return (
    <div
      role="status"
      className="rounded-lg border border-estimated/50 bg-estimated/10 p-3 text-sm text-estimated"
    >
      {children}
    </div>
  );
}

export const inputClass =
  "w-full rounded-md border border-line bg-ink px-3 py-2 font-mono text-sm text-bone placeholder:text-mist/60";

/**
 * A text field with its label, an optional hint and an error message, all linked by
 * react-aria (aria-labelledby, aria-describedby, aria-invalid).
 */
export function Field({
  label,
  value,
  onChange,
  hint,
  error,
  placeholder,
  inputMode,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  hint?: string;
  error?: string | undefined;
  placeholder?: string;
  inputMode?: "numeric" | "text";
}) {
  return (
    <TextField
      value={value}
      onChange={onChange}
      isInvalid={error !== undefined}
      validationBehavior="aria"
      className="block space-y-1"
    >
      <Label className="block text-xs text-mist">{label}</Label>
      <Input placeholder={placeholder} inputMode={inputMode} className={inputClass} />
      {hint !== undefined && (
        <Text slot="description" className="block text-xs text-mist">
          {hint}
        </Text>
      )}
      <FieldError className="block text-xs text-unknown">{error}</FieldError>
    </TextField>
  );
}
