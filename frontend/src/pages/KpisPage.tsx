import { useQuery } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { Link } from "react-router";

import { api } from "@/api/client";
import type { components } from "@/api/schema";

/**
 * Phase 1 KPIs and AI cost (requirements §14, NFR-14, NFR-15).
 *
 * What each bid measures on its own: how many AI proposals a person rejected (false
 * detections), how many items they had to add (missed items, a lower bound), duplicate groups
 * still open, time on task, how often agents escalated or failed, and what the AI cost
 * against the bid's budget and the target per tender. Accuracy against verified truth is in
 * the Phase 1 exit report, measured on the golden set.
 */

type Kpis = components["schemas"]["KpisOut"];
type Row = components["schemas"]["BidKpisOut"];

function percent(value: number | null | undefined): string {
  return value == null ? "–" : `${(100 * value).toFixed(1)}%`;
}

function over(value: number | null | undefined, limit: number): boolean {
  return value != null && value > limit;
}

export function KpisPage() {
  const [open, setOpen] = useState<string | null>(null);
  const kpis = useQuery({
    queryKey: ["kpis"],
    queryFn: async (): Promise<Kpis> => {
      const { data } = await api.GET("/kpis");
      if (!data) throw new Error("Could not read the KPIs");
      return data;
    },
  });
  const data = kpis.data;
  return (
    <section className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">KPIs</h1>
        <p className="text-sm text-muted-foreground">
          What each bid measures on its own, and what its AI work cost. Accuracy against verified
          takeoffs is in the Phase 1 exit report.
        </p>
      </div>
      {kpis.isLoading && <p className="text-sm">Reading…</p>}
      {data && (
        <>
          <dl className="grid gap-4 sm:grid-cols-4" aria-label="Totals">
            <Stat label="Agent runs" value={String(data.agent_runs)} />
            <Stat label="Escalation rate" value={percent(data.escalation_rate)} note="monitor" />
            <Stat
              label="Workflow and tool success"
              value={percent(data.success_rate)}
              note="target ≥98%"
              warn={data.success_rate != null && data.success_rate < 0.98}
            />
            <Stat
              label="AI cost"
              value={`SGD ${Number(data.cost_sgd).toFixed(2)}`}
              note={
                data.target_cost_sgd
                  ? `target SGD ${Number(data.target_cost_sgd).toFixed(2)} per tender (to be confirmed)`
                  : "no target set"
              }
            />
          </dl>
          <table className="w-full text-sm" aria-label="Bids">
            <thead className="text-left text-xs text-muted-foreground">
              <tr>
                <th className="py-2 pr-3">Bid</th>
                <th className="py-2 pr-3 text-right">AI items</th>
                <th className="py-2 pr-3 text-right">False detections</th>
                <th className="py-2 pr-3 text-right">Missed (added by hand)</th>
                <th className="py-2 pr-3 text-right">Open duplicates</th>
                <th className="py-2 pr-3 text-right">Time on task</th>
                <th className="py-2 pr-3 text-right">Agent runs</th>
                <th className="py-2 pr-3 text-right">AI cost</th>
              </tr>
            </thead>
            <tbody>
              {data.bids.map((row) => (
                <Fragment key={row.bid_id}>
                  <BidRow
                    row={row}
                    open={open === row.bid_id}
                    onToggle={() => setOpen(open === row.bid_id ? null : row.bid_id)}
                  />
                  {open === row.bid_id && <CostRows row={row} />}
                </Fragment>
              ))}
            </tbody>
          </table>
        </>
      )}
    </section>
  );
}

function Stat({ label, value, note, warn }: { label: string; value: string; note?: string; warn?: boolean }) {
  return (
    <div className="rounded-md border p-3">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={`text-lg font-medium ${warn ? "text-amber-700" : ""}`}>{value}</dd>
      {note && <dd className="text-xs text-muted-foreground">{note}</dd>}
    </div>
  );
}

function BidRow({ row, open, onToggle }: { row: Row; open: boolean; onToggle: () => void }) {
  const overBudget = row.budget_sgd != null && Number(row.cost_sgd) > Number(row.budget_sgd);
  return (
    <tr className="border-t">
      <td className="py-2 pr-3">
        <Link to={`/bids/${row.bid_id}`} className="underline">
          {row.human_id}
        </Link>
      </td>
      <td className="py-2 pr-3 text-right">{row.ai_items}</td>
      <td className={`py-2 pr-3 text-right ${over(row.false_detection_rate, 0.05) ? "text-amber-700" : ""}`}>
        {percent(row.false_detection_rate)}
      </td>
      <td className={`py-2 pr-3 text-right ${over(row.missed_item_rate, 0.05) ? "text-amber-700" : ""}`}>
        {percent(row.missed_item_rate)}
      </td>
      <td className={`py-2 pr-3 text-right ${row.unresolved_duplicates ? "text-amber-700" : ""}`}>
        {row.unresolved_duplicates}
      </td>
      <td className="py-2 pr-3 text-right">{row.minutes_on_task} min</td>
      <td className="py-2 pr-3 text-right">{row.agent_runs}</td>
      <td className={`py-2 pr-3 text-right ${overBudget ? "text-red-700" : ""}`}>
        <button type="button" className="underline" onClick={onToggle} aria-expanded={open}>
          SGD {Number(row.cost_sgd).toFixed(2)}
        </button>
      </td>
    </tr>
  );
}

function CostRows({ row }: { row: Row }) {
  if (row.cost_by_model.length === 0) {
    return (
      <tr>
        <td />
        <td colSpan={7} className="pb-2 text-xs text-muted-foreground">
          No AI runs on this bid.
        </td>
      </tr>
    );
  }
  return (
    <>
      {row.cost_by_model.map((line) => (
        <tr key={`${line.route}-${line.provider}-${line.model}`} className="text-xs text-muted-foreground">
          <td />
          <td colSpan={5} className="pr-3">
            {line.route} · {line.provider} · {line.model}
          </td>
          <td className="pr-3 text-right">{line.runs}</td>
          <td className="pr-3 text-right">SGD {Number(line.cost_sgd).toFixed(2)}</td>
        </tr>
      ))}
    </>
  );
}
