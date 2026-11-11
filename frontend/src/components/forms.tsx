import { type ReactNode, useId } from "react";
import { inputClass } from "./ui";

type InputProps = {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: "text" | "number" | "datetime-local";
  hint?: string;
  min?: number;
};

/** A native labelled input: the label is the accessible name, the hint its description. */
export function TextInput({ label, value, onChange, type = "text", hint, min }: InputProps) {
  const id = useId();
  const hintId = `${id}-hint`;
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-xs text-mist">
        {label}
      </label>
      <input
        id={id}
        type={type}
        min={min}
        value={value}
        aria-describedby={hint === undefined ? undefined : hintId}
        onChange={(event) => onChange(event.currentTarget.value)}
        className={inputClass}
      />
      {hint !== undefined && (
        <p id={hintId} className="text-xs text-mist">
          {hint}
        </p>
      )}
    </div>
  );
}

/** A native select; its options are `[value, label]` pairs. */
export function SelectInput({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  options: readonly (readonly [string, string])[];
}): ReactNode {
  const id = useId();
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-xs text-mist">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(event.currentTarget.value)}
        className={inputClass}
      >
        {options.map(([optionValue, text]) => (
          <option key={optionValue} value={optionValue}>
            {text}
          </option>
        ))}
      </select>
    </div>
  );
}
