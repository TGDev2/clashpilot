import type { ReactNode } from "react";
import { Link } from "react-aria-components";
import { api, settle } from "../api/client";
import type { components } from "../api/schema";
import { useApi } from "../api/useApi";
import { useScheduleZone } from "../api/useZone";
import { ProblemNotice } from "../components/ProblemNotice";
import { ProvenanceBadge } from "../components/ProvenanceBadge";
import { StockChart } from "../components/StockChart";
import { PageTitle, Section } from "../components/ui";
import { fr } from "../i18n/fr";
import { formatDuration, formatInZone, formatNumber, resourceLabel } from "../lib/format";
import { useVillage } from "../state/village";

type Forecast = components["schemas"]["ForecastOut"];
type Point = components["schemas"]["SeriesPoint"];
type Estimate = components["schemas"]["Estimate"];

const INCOMES = ["low", "expected", "high"] as const;

/** A predicted value: its number, or the Inconnu badge (never 0) when the model has none. */
function EstimateCell({ estimate }: { estimate: Estimate }) {
  if (estimate.value === null || estimate.value === undefined || estimate.status === "unknown") {
    return <ProvenanceBadge status="unknown" />;
  }
  return <span className="font-mono">{formatNumber(Math.round(estimate.value))}</span>;
}

function costText(cost: Record<string, number>): string {
  return Object.entries(cost)
    .map(([resource, amount]) => `${resourceLabel(resource)} ${formatNumber(amount)}`)
    .join(", ");
}

function SeriesSection({
  resource,
  points,
  timezone,
}: {
  resource: string;
  points: readonly Point[];
  timezone: string;
}) {
  const label = resourceLabel(resource);
  const known = points.some((point) =>
    INCOMES.some((income) => point[income].status === "estimated"),
  );
  return (
    <Section title={label}>
      <div>
        <ProvenanceBadge status={known ? "estimated" : "unknown"} />
      </div>
      <StockChart points={points} title={fr.forecast.seriesAlt(label)} />
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="text-xs text-mist">
              <th scope="col" className="py-1 pr-4 font-medium">
                {fr.forecast.date}
              </th>
              {INCOMES.map((income) => (
                <th key={income} scope="col" className="py-1 pr-4 font-medium">
                  {fr.forecast[income]}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {points.map((point) => (
              <tr key={point.t} className="border-t border-line/60">
                <td className="py-1 pr-4 font-mono text-mist">{formatInZone(point.t, timezone)}</td>
                {INCOMES.map((income) => (
                  <td key={income} className="py-1 pr-4">
                    <EstimateCell estimate={point[income]} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

function Rows({ children }: { children: ReactNode }) {
  return <ul className="divide-y divide-line/60 text-sm">{children}</ul>;
}

function Estimated() {
  return (
    <div>
      <ProvenanceBadge status="estimated" />
    </div>
  );
}

function Saturations({ forecast, timezone }: { forecast: Forecast; timezone: string }) {
  return (
    <Section title={fr.forecast.saturations}>
      {forecast.saturation.length === 0 ? (
        <p className="text-sm text-mist">{fr.forecast.noSaturation}</p>
      ) : (
        <>
          <Estimated />
          <Rows>
            {forecast.saturation.map((window) => (
              <li
                key={`${window.resource}-${window.start}`}
                className="flex flex-wrap gap-x-4 gap-y-1 py-2"
              >
                <span className="text-bone">{resourceLabel(window.resource)}</span>{" "}
                <span className="font-mono text-mist">
                  {formatInZone(window.start, timezone)} – {formatInZone(window.end, timezone)}
                </span>{" "}
                <span className="font-mono text-mist">
                  {formatNumber(Math.round(window.spilled))} {fr.forecast.spilled}
                </span>
              </li>
            ))}
          </Rows>
        </>
      )}
    </Section>
  );
}

function Blocking({ forecast }: { forecast: Forecast }) {
  const blocking = forecast.blocking;
  return (
    <Section title={fr.forecast.blocking}>
      {blocking === null || blocking === undefined ? (
        <p className="text-sm text-mist">{fr.forecast.noBlocking}</p>
      ) : (
        <>
          <Estimated />
          <p className="text-sm">
            <span className="text-bone">{resourceLabel(blocking.resource)}</span>{" "}
            <span className="font-mono text-mist">
              {fr.forecast.delayed} : {formatDuration(blocking.delayed_s)}
            </span>
          </p>
          <ul className="flex flex-wrap gap-2 font-mono text-xs text-mist">
            {blocking.tasks.map((task) => (
              <li key={task} className="rounded border border-line px-2 py-0.5">
                {task}
              </li>
            ))}
          </ul>
        </>
      )}
    </Section>
  );
}

function Walls({ forecast, timezone }: { forecast: Forecast; timezone: string }) {
  return (
    <Section title={fr.forecast.walls}>
      {forecast.walls.length === 0 ? (
        <p className="text-sm text-mist">{fr.forecast.noWalls}</p>
      ) : (
        <>
          <Estimated />
          <Rows>
            {forecast.walls.map((wall) => (
              <li
                key={`${wall.data_id}-${wall.instance}-${wall.at}`}
                className="flex flex-wrap gap-x-4 gap-y-1 py-2"
              >
                <span className="text-bone">{wall.name}</span>{" "}
                <span className="text-mist">{fr.common.level(wall.level)}</span>{" "}
                <span className="font-mono text-mist">{costText(wall.cost)}</span>{" "}
                <span className="font-mono text-mist">{formatInZone(wall.at, timezone)}</span>
              </li>
            ))}
          </Rows>
        </>
      )}
    </Section>
  );
}

/** The forecast of the latest plan: stock series, saturations, blocking resource and walls. */
export function Resources() {
  const { villageId } = useVillage();
  const timezone = useScheduleZone(villageId);
  const forecast = useApi<Forecast>(
    () =>
      settle(() =>
        api().GET("/api/villages/{village_id}/forecast", {
          params: { path: { village_id: villageId ?? "" } },
        }),
      ),
    villageId,
  );
  const data = forecast.data;
  const everythingUnknown =
    data !== undefined &&
    Object.values(data.series).every((points) =>
      points.every((point) => INCOMES.every((income) => point[income].status === "unknown")),
    );
  return (
    <>
      <PageTitle>{fr.forecast.title}</PageTitle>
      <div className="space-y-6">
        <p className="text-sm text-mist">{fr.forecast.intro}</p>
        {forecast.loading && data === undefined && (
          <p className="text-mist">{fr.forecast.loadingHint}</p>
        )}
        <ProblemNotice problem={forecast.problem} />
        {everythingUnknown && (
          <p className="text-sm text-mist">
            {fr.forecast.unknownHint}{" "}
            <Link href="/importer#saisie" className="text-signal underline underline-offset-2">
              {fr.forecast.unknownLink}
            </Link>
          </p>
        )}
        {data !== undefined && (
          <>
            {Object.entries(data.series).map(([resource, points]) => (
              <SeriesSection
                key={resource}
                resource={resource}
                points={points}
                timezone={timezone}
              />
            ))}
            <Saturations forecast={data} timezone={timezone} />
            <Blocking forecast={data} />
            <Walls forecast={data} timezone={timezone} />
          </>
        )}
      </div>
    </>
  );
}
