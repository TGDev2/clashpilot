import createClient from "openapi-fetch";
import { fr } from "../i18n/fr";
import type { components, paths } from "./schema";

export type VillageState = components["schemas"]["VillageState"];
export type Provenance = components["schemas"]["Provenance"];
export type PackSummary = components["schemas"]["PackSummary"];
export type DiffOut = components["schemas"]["DiffOut"];
export type SyncOut = components["schemas"]["SyncOut"];
export type ManualOut = components["schemas"]["ManualOut"];

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
export type ApiProblem = { code: string; message: string; errors: string[] };

export function problemOf(error: unknown, status: number): ApiProblem {
  const detail = (error as { detail?: unknown } | null | undefined)?.detail;
  if (detail !== null && typeof detail === "object") {
    const { code, message, errors } = detail as {
      code?: unknown;
      message?: unknown;
      errors?: unknown;
    };
    if (typeof message === "string") {
      return {
        code: typeof code === "string" ? code : "error",
        message,
        errors: Array.isArray(errors)
          ? errors.filter((e): e is string => typeof e === "string")
          : [],
      };
    }
  }
  return { code: "error", message: fr.common.genericError(status), errors: [] };
}

export const networkProblem: ApiProblem = {
  code: "network",
  message: fr.common.networkError,
  errors: [],
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
