/** Stable React keys for rows that may repeat: the n-th repeat of a base gets the suffix #n. */
export function uniqueKeys(bases: string[]): string[] {
  const seen = new Map<string, number>();
  return bases.map((base) => {
    const n = seen.get(base) ?? 0;
    seen.set(base, n + 1);
    return n === 0 ? base : `${base}#${n}`;
  });
}
