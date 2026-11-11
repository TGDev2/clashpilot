import createClient from "openapi-fetch";
import { fr } from "../i18n/fr";
import type { components, paths } from "./schema";

export type VillageState = components["schemas"]["VillageState"];
export type Provenance = components["schemas"]["Provenance"];
export type PackSummary = components["schemas"]["PackSummary"];
export type DiffOut = components["schemas"]["DiffOut"];
export type SyncOut = components["schemas"]["SyncOut"];
export type ManualOut = components["schemas"]["ManualOut"];
export type ScheduleProfile = components["schemas"]["ScheduleProfile"];
export type WeeklyWindow = components["schemas"]["WeeklyWindow"];
export type PlanOut = components["schemas"]["PlanOut"];
export type RecommendationsOut = components["schemas"]["RecommendationsOut"];
export type ActionOut = components["schemas"]["ActionOut"];
export type ProofOut = components["schemas"]["ProofOut"];
export type AssignmentOut = components["schemas"]["AssignmentOut"];
type PreviewSchema = components["schemas"]["SchedulePreview"];
/** As the client hands it over: each window is a `[start, end]` pair of UTC epochs, typed loosely. */
export type SchedulePreview = {
  [K in keyof PreviewSchema]: PreviewSchema[K] extends [number, number][]
    ? number[][]
    : PreviewSchema[K];
};

let client: ReturnType<typeof createClient<paths>> | undefined;

/**
 * The typed client. `fetch` is looked up at call time (tests replace it) and the base URL is
 * the page origin: the SPA and the API are served by the same process.
 */
export function api(): ReturnType<typeof createClient<paths>> {
  client ??= createClient<paths>({
    baseUrl: window.location.origin,
    fetch: (request) => globalThis.fetch(request),
  });
  return client;
}

/** What the user is told when a call fails: the API's own French message when it sent one. */
/** `missing` names the inputs of a 409 `missing_input`. */
export type ApiProblem = {
  code: string;
  message: string;
  errors: string[];
  missing: string[];
};

export function problemOf(error: unknown, status: number): ApiProblem {
  const detail = (error as { detail?: unknown } | null | undefined)?.detail;
  if (detail !== null && typeof detail === "object") {
    const { code, message, errors, missing } = detail as {
      code?: unknown;
      message?: unknown;
      errors?: unknown;
      missing?: unknown;
    };
    if (typeof message === "string") {
      return {
        code: typeof code === "string" ? code : "error",
        message,
        errors: Array.isArray(errors)
          ? errors.filter((e): e is string => typeof e === "string")
          : [],
        missing: Array.isArray(missing)
          ? missing.filter((m): m is string => typeof m === "string")
          : [],
      };
    }
  }
  return { code: "error", message: fr.common.genericError(status), errors: [], missing: [] };
}

export const networkProblem: ApiProblem = {
  code: "network",
  message: fr.common.networkError,
  errors: [],
  missing: [],
};

export type Outcome<T> = { ok: true; data: T } | { ok: false; problem: ApiProblem };

/** Run a client call and fold its result, thrown network failures included, into an Outcome. */
export async function settle<T>(
  call: () => Promise<{ data?: T; error?: unknown; response: Response }>,
): Promise<Outcome<T>> {
  try {
    const { data, error, response } = await call();
    if (response.ok && data !== undefined) {
      return { ok: true, data };
    }
    return { ok: false, problem: problemOf(error, response.status) };
  } catch {
    return { ok: false, problem: networkProblem };
  }
}
