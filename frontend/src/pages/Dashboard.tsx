import { useEffect, useState } from "react";
import { Link } from "react-aria-components";
import { api, type Provenance, settle } from "../api/client";
import { useApi } from "../api/useApi";
import { useLatestPlan } from "../api/usePlan";
import { PlanProgress } from "../components/PlanProgress";
import { ProvenanceBadge } from "../components/ProvenanceBadge";
import { StaleBanner } from "../components/replan/StaleBanner";
import { Banner, PageTitle, ProblemAlert, Section } from "../components/ui";
import { fr } from "../i18n/fr";
import { formatDate, formatNumber, formatRemaining } from "../lib/format";
import { uniqueKeys } from "../lib/keys";
import { useVillage } from "../state/village";

const RESOURCE_ORDER = [
  "gold",
  "elixir",
  "dark_elixir",
  "gems",
  "shiny_ore",
  "glowy_ore",
  "starry_ore",
] as const;

/** The current time, refreshed every 30 s so the countdowns follow the clock. */
function useNow(): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, []);
  return now;
}

function Row({ children }: { children: React.ReactNode }) {
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line/60 py-2 last:border-b-0">
      {children}
    </li>
  );
}

function ProvenanceNote({ provenance }: { provenance: Provenance }) {
  const source = fr.source[provenance.source as keyof typeof fr.source] ?? provenance.source;
  const date = provenance.as_of === null ? "" : ` ${fr.common.asOf(formatDate(provenance.as_of))}`;
  return (
    <span className="text-xs text-mist">
      {source}
      {date}
    </span>
  );
}

export function Dashboard() {
  const { villageId } = useVillage();
  const now = useNow();
  const packs = useApi(() => settle(() => api().GET("/api/gamedata/packs")), "packs");
  const state = useApi(
    () =>
      settle(() =>
        api().GET("/api/villages/{village_id}/state", {
          params: { path: { village_id: villageId ?? "" } },
        }),
      ),
    villageId,
  );

  // The plan is optional here: no plan or a failed read shows no indicator, nothing else.
  const plan = useLatestPlan(villageId);
  const activePack = packs.data?.find((pack) => pack.active);
  const village = state.data;
  const itemKeys = uniqueKeys(
    (village?.items ?? []).map((i) => `${i.data_id}-${i.level}-${i.village}-${i.section}`),
  );

  return (
    <>
      <PageTitle>{fr.dashboard.title}</PageTitle>
      <div className="space-y-6">
        {activePack?.fictional && <Banner>{fr.dashboard.fictionalBanner}</Banner>}
        {packs.data !== undefined && activePack === undefined && (
          <Banner>
            {fr.dashboard.noPack}{" "}
            <Link href="/importer#packs" className="underline underline-offset-2">
              {fr.dashboard.noPackLink}
            </Link>
          </Banner>
        )}
        {villageId === null && (
          <p className="text-mist">
            {fr.dashboard.noVillage}{" "}
            <Link href="/importer" className="text-signal underline underline-offset-2">
              {fr.dashboard.noVillageLink}
            </Link>
          </p>
        )}
        <ProblemAlert problem={state.problem} />
        {villageId !== null && plan.data?.stale === true && (
          <StaleBanner villageId={villageId} planId={plan.data.plan_id} onReplanned={plan.reload} />
        )}
        {plan.data !== undefined && (
          <Section title={fr.dashboard.progress}>
            <PlanProgress plan={plan.data} nowMs={now} />
          </Section>
        )}
        {village && (
          <>
            <p className="font-mono text-xs text-mist">
              {fr.dashboard.snapshot(formatDate(village.exported_at))}
            </p>
            <Section title={fr.dashboard.resources}>
              <ul>
                {RESOURCE_ORDER.map((name) => {
                  const resource = village.resources[name];
                  return (
                    <Row key={name}>
                      <span className="w-40 text-bone">{fr.resources[name]}</span>
                      {resource && resource.value !== null && (
                        <span className="font-mono text-bone tabular-nums">
                          {formatNumber(resource.value)}
                        </span>
                      )}
                      <ProvenanceBadge status={resource?.status ?? "unknown"} />
                      {resource && <ProvenanceNote provenance={resource} />}
                    </Row>
                  );
                })}
                <Row>
                  <span className="w-40 text-bone">{fr.dashboard.builders}</span>
                  {village.builders.value !== null && (
                    <span className="font-mono text-bone tabular-nums">
                      {formatNumber(village.builders.value)}
                    </span>
                  )}
                  <ProvenanceBadge status={village.builders.status} />
                  <ProvenanceNote provenance={village.builders} />
                </Row>
              </ul>
            </Section>
            <Section title={fr.dashboard.running}>
              {village.running.length === 0 ? (
                <p className="text-mist">{fr.dashboard.noRunning}</p>
              ) : (
                <ul>
                  {village.running.map((job) => {
                    const remaining = formatRemaining(job.finish_at, now);
                    return (
                      <Row key={`${job.data_id}-${job.finish_at}-${job.village}-${job.to_level}`}>
                        <span className="text-bone">
                          {job.name ?? fr.common.unnamed(job.data_id)}
                        </span>
                        <span className="text-sm text-mist">
                          {fr.dashboard.toLevel(job.to_level)}
                        </span>
                        <span className="font-mono text-sm text-signal tabular-nums">
                          {remaining ?? fr.dashboard.finished}
                        </span>
                        <ProvenanceBadge status={job.provenance.status} />
                      </Row>
                    );
                  })}
                </ul>
              )}
            </Section>
            <Section title={fr.dashboard.items}>
              <ul>
                {village.items.map((item, position) => (
                  <Row key={itemKeys[position]}>
                    <span className="text-bone">
                      {item.name ?? fr.common.unnamed(item.data_id)}
                    </span>
                    <span className="text-sm text-mist">
                      {item.level === null ? fr.common.unknownLevel : fr.common.level(item.level)}
                    </span>
                    {item.count > 1 && (
                      <span className="font-mono text-sm text-mist">
                        {fr.dashboard.count(item.count)}
                      </span>
                    )}
                    <ProvenanceBadge status={item.provenance.status} />
                  </Row>
                ))}
              </ul>
            </Section>
          </>
        )}
      </div>
    </>
  );
}
