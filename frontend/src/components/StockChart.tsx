import type { components } from "../api/schema";
import { fr } from "../i18n/fr";
import { formatNumber } from "../lib/format";

type Point = components["schemas"]["SeriesPoint"];
type Income = "low" | "expected" | "high";

const WIDTH = 640;
const HEIGHT = 220;
const PAD = { left: 64, right: 12, top: 12, bottom: 24 };

// One colour and one dash per income: the lines stay apart without colour vision.
const LINES: { key: Income; label: string; stroke: string; dash: string | undefined }[] = [
  { key: "low", label: fr.forecast.low, stroke: "var(--color-unknown)", dash: "6 4" },
  { key: "expected", label: fr.forecast.expected, stroke: "var(--color-signal)", dash: undefined },
  { key: "high", label: fr.forecast.high, stroke: "var(--color-confirmed)", dash: "2 4" },
];

/** The stock of one resource under the three incomes; the unknown points leave gaps in the lines. */
export function StockChart({ points, title }: { points: readonly Point[]; title: string }) {
  const values = points.flatMap((point) =>
    LINES.map((line) => point[line.key].value).filter((value): value is number => value != null),
  );
  if (values.length === 0 || points.length === 0) {
    return null;
  }
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const span = hi - lo || 1;
  const first = points[0]?.t ?? 0;
  const last = points[points.length - 1]?.t ?? first;
  const x = (t: number) =>
    PAD.left +
    (last === first ? 0 : ((t - first) / (last - first)) * (WIDTH - PAD.left - PAD.right));
  const y = (value: number) =>
    PAD.top + (1 - (value - lo) / span) * (HEIGHT - PAD.top - PAD.bottom);

  function path(key: Income): string {
    let drawing = false;
    return points
      .map((point) => {
        const value = point[key].value;
        if (value == null) {
          drawing = false;
          return "";
        }
        const command = drawing ? "L" : "M";
        drawing = true;
        return `${command}${x(point.t).toFixed(1)} ${y(value).toFixed(1)}`;
      })
      .join(" ")
      .trim();
  }

  return (
    <figure className="space-y-2">
      <svg
        role="img"
        aria-label={title}
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="h-auto w-full max-w-2xl"
      >
        <title>{title}</title>
        {[lo, hi].map((value) => (
          <g key={value}>
            <line
              x1={PAD.left}
              x2={WIDTH - PAD.right}
              y1={y(value)}
              y2={y(value)}
              stroke="var(--color-line)"
              strokeWidth={1}
            />
            <text
              x={PAD.left - 6}
              y={y(value)}
              textAnchor="end"
              dominantBaseline="middle"
              fontSize={11}
              fill="var(--color-mist)"
            >
              {formatNumber(Math.round(value))}
            </text>
          </g>
        ))}
        {LINES.map((line) => {
          const d = path(line.key);
          return d === "" ? null : (
            <path
              key={line.key}
              data-series={line.key}
              d={d}
              fill="none"
              stroke={line.stroke}
              strokeWidth={2}
              strokeDasharray={line.dash}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          );
        })}
      </svg>
      <figcaption className="flex flex-wrap gap-x-4 text-xs text-mist">
        {LINES.map((line) => (
          <span key={line.key} className="inline-flex items-center gap-1.5">
            <svg width="22" height="8" aria-hidden="true">
              <line
                x1="0"
                x2="22"
                y1="4"
                y2="4"
                stroke={line.stroke}
                strokeWidth={2}
                strokeDasharray={line.dash}
              />
            </svg>
            {line.label}
          </span>
        ))}
      </figcaption>
    </figure>
  );
}
