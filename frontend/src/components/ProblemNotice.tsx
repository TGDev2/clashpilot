import { Link } from "react-aria-components";
import type { ApiProblem } from "../api/client";
import { fr } from "../i18n/fr";

/**
 * An API failure announced in an alert: the API's own French message, the detail lines it sent and,
 * for a `missing_input`, the way to the manual input.
 */
export function ProblemNotice({ problem }: { problem: ApiProblem | undefined | null }) {
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
      {problem.code === "missing_input" && (
        <p className="mt-2">
          <Link href="/importer#saisie" className="text-signal underline underline-offset-2">
            {fr.form.missingManual}
          </Link>
        </p>
      )}
    </div>
  );
}
