import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, apiErrorMessage } from "@/api/client";
import type { components } from "@/api/schema";
import { Button } from "@/components/ui/button";

/**
 * Labour estimation (FR-LAB-01, 02, 03).
 *
 * Each line of the bill shows its baseline hours (quantity times the productivity library's
 * man-hours per unit, with the entry's source), every multiplier applied to it (its value,
 * where it applies, who confirmed it, its source and rationale), the hours they come to, and
 * the trade's hourly rate. A multiplier the platform proposes changes nothing until an
 * estimator confirms it. The hourly rates are built up line by line from the rate table in
 * force on the day the bid is priced, and the table's effective date is shown.
 */

type Labour = components["schemas"]["LabourOut"];
type Line = components["schemas"]["LabourLineOut"];
type Condition = components["schemas"]["ConditionOut"];
type Trade = components["schemas"]["TradeRateOut"];

function hours(value: string | null | undefined): string {
  return value == null ? "–" : Number(value).toFixed(2);
}

export function LabourEstimate({
  bidId,
  onError,
}: {
  bidId: string;
  onError: (caught: unknown) => void;
}) {
  const client = useQueryClient();
  const path = { params: { path: { bid_id: bidId } } };
  const key = ["boq", bidId, "labour"];
  const labour = useQuery({
    queryKey: key,
    queryFn: async (): Promise<Labour | null> => {
      const { data } = await api.GET("/bids/{bid_id}/labour", path);
      return data ?? null;
    },
  });
  const done = (data: Labour) => {
    client.setQueryData(key, data);
    void client.invalidateQueries({ queryKey: ["boq", bidId, "build-up"] });
  };
  const propose = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST("/bids/{bid_id}/labour/conditions/propose", path);
      if (error || !data) throw new Error(apiErrorMessage(error, "Could not propose conditions"));
      return data;
    },
    onSuccess: done,
    onError,
  });
  const decide = useMutation({
    mutationFn: async (body: components["schemas"]["ConditionIn"]) => {
      const { data, error } = await api.PUT("/bids/{bid_id}/labour/conditions", {
        ...path,
        body,
      });
      if (error || !data) throw new Error(apiErrorMessage(error, "Could not save the condition"));
      return data;
    },
    onSuccess: done,
    onError,
  });

  const data = labour.data;
  if (!data) return null;
  const catalogue = data.catalogue;
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-medium">
        Labour{" "}
        <span className="text-sm font-normal text-muted-foreground">
          {hours(data.hours)} man-hours ({hours(data.baseline_hours)} baseline), SGD {data.cost}
          {data.without_hours > 0
            ? ` · ${data.without_hours} line(s) with no productivity entry`
            : ""}
        </span>
      </h2>

      <Conditions
        data={data}
        busy={propose.isPending || decide.isPending}
        onPropose={() => propose.mutate()}
        onDecide={(body) => decide.mutate(body)}
      />

      <table className="w-full text-sm" aria-label="Labour">
        <thead className="text-left text-xs text-muted-foreground">
          <tr>
            <th className="py-2 pr-3">Item</th>
            <th className="py-2 pr-3">Description</th>
            <th className="py-2 pr-3 text-right">Qty</th>
            <th className="py-2 pr-3">Baseline</th>
            <th className="py-2 pr-3">Multipliers</th>
            <th className="py-2 pr-3 text-right">Hours</th>
            <th className="py-2 pr-3 text-right">Rate</th>
            <th className="py-2 text-right">Cost</th>
          </tr>
        </thead>
        <tbody>
          {data.lines.map((line) => (
            <LabourRow key={line.line_id} line={line} />
          ))}
        </tbody>
      </table>

      <div className="grid gap-4 md:grid-cols-2">
        <Totals label="Labour by system" rows={data.by_section} />
        <Totals label="Labour by trade" rows={data.by_trade} />
      </div>

      <div className="space-y-1">
        <h3 className="font-medium">
          Labour rates{" "}
          <span className="text-sm font-normal text-muted-foreground">
            {catalogue.rate_table_effective_from
              ? `the table effective from ${catalogue.rate_table_effective_from} (${catalogue.rate_table_source}), for a bid priced on ${data.priced_on}`
              : `no rate table is in force on ${data.priced_on}`}
          </span>
        </h3>
        {catalogue.trades.map((trade) => (
          <TradeRate key={trade.trade} trade={trade} />
        ))}
      </div>
    </div>
  );
}

function LabourRow({ line }: { line: Line }) {
  return (
    <tr className="border-t align-top" data-hours={line.hours == null ? "none" : "worked"}>
      <td className="py-1 pr-3">{line.reference}</td>
      <td className="py-1 pr-3">
        {line.description}
        {line.level && <span className="text-xs text-muted-foreground"> · {line.level}</span>}
      </td>
      <td className="py-1 pr-3 text-right">
        {Number(line.quantity)} {line.unit}
      </td>
      <td className="py-1 pr-3 text-xs">
        {line.baseline_hours == null ? (
          <span className="font-medium text-red-700">{line.reason}</span>
        ) : (
          <>
            {hours(line.baseline_hours)} h
            <div className="text-muted-foreground">
              {Number(line.hours_per_unit)} h per {line.unit} · {line.productivity_source}
            </div>
          </>
        )}
      </td>
      <td className="py-1 pr-3 text-xs">
        {line.multipliers.length === 0 && line.baseline_hours != null && "none"}
        {line.multipliers.map((item) => (
          <div key={item.key} title={`${item.rationale} Source: ${item.source}`}>
            × {Number(item.value).toFixed(2)} {item.label}
            <div className="text-muted-foreground">
              {item.scope} · confirmed by {item.confirmed_by} · {item.source}
            </div>
          </div>
        ))}
        <ByLevel line={line} />
      </td>
      <td className="py-1 pr-3 text-right">{hours(line.hours)}</td>
      <td className="py-1 pr-3 text-right">
        {line.hourly_rate == null ? "–" : Number(line.hourly_rate).toFixed(2)}
        {line.trade && (
          <div className="text-xs text-muted-foreground">{line.trade.replaceAll("_", " ")}</div>
        )}
      </td>
      <td className="py-1 text-right">{line.cost ?? "–"}</td>
    </tr>
  );
}

/** A line billed for the building, level by level: where its hours and its factor come from. */
function ByLevel({ line }: { line: Line }) {
  const parts = line.by_level ?? [];
  const [only] = parts;
  if (only === undefined) return null;
  if (parts.length === 1) {
    return <div className="text-muted-foreground">all on {only.level ?? "no level"}</div>;
  }
  return (
    <div className="mt-1 text-muted-foreground" data-testid={`by-level-${line.line_id}`}>
      Overall × {Number(line.factor).toFixed(4)}, by level:
      {parts.map((part) => (
        <div key={part.level ?? "none"}>
          {part.level ?? "no level"}: {Number(part.quantity)} {line.unit},{" "}
          {hours(part.baseline_hours)} h × {Number(part.factor).toFixed(2)} = {hours(part.hours)} h
        </div>
      ))}
    </div>
  );
}

function Totals({
  label,
  rows,
}: {
  label: string;
  rows: components["schemas"]["SubtotalOut"][];
}) {
  return (
    <table className="w-full text-sm" aria-label={label}>
      <caption className="py-1 text-left font-medium">{label}</caption>
      <thead className="text-left text-xs text-muted-foreground">
        <tr>
          <th className="py-1 pr-3" />
          <th className="py-1 pr-3 text-right">Baseline h</th>
          <th className="py-1 pr-3 text-right">Hours</th>
          <th className="py-1 text-right">Cost</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.key} className="border-t">
            <td className="py-1 pr-3">{row.label}</td>
            <td className="py-1 pr-3 text-right">{hours(row.baseline_hours)}</td>
            <td className="py-1 pr-3 text-right">{hours(row.hours)}</td>
            <td className="py-1 text-right">{Number(row.cost).toFixed(2)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Conditions({
  data,
  busy,
  onPropose,
  onDecide,
}: {
  data: Labour;
  busy: boolean;
  onPropose: () => void;
  onDecide: (body: components["schemas"]["ConditionIn"]) => void;
}) {
  const [key, setKey] = useState("");
  const [level, setLevel] = useState("");
  const [basis, setBasis] = useState("");
  const state = (row: Condition) =>
    row.state === "proposed"
      ? `proposed by ${row.proposed_by === "platform" ? "the platform" : row.proposed_by}`
      : `${row.state} by ${row.decided_by}`;
  return (
    <div className="space-y-1">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-medium">Site conditions</h3>
        <Button variant="outline" onClick={onPropose} disabled={busy}>
          Propose from the bid's parameters
        </Button>
      </div>
      {data.conditions.length > 0 && (
        <table className="w-full text-sm" aria-label="Site conditions">
          <tbody>
            {data.conditions.map((row) => (
              <tr key={row.id} className="border-t align-top" data-state={row.state}>
                <td className="py-1 pr-3">
                  {row.label}
                  <div className="text-xs text-muted-foreground">{row.rationale}</div>
                </td>
                <td className="py-1 pr-3">
                  × {row.value == null ? "?" : Number(row.value).toFixed(2)}
                  <div className="text-xs text-muted-foreground">{row.source}</div>
                </td>
                <td className="py-1 pr-3">{row.level ?? "the whole bid"}</td>
                <td className="py-1 pr-3 text-xs">
                  {state(row)}
                  <div className="text-muted-foreground">{row.basis}</div>
                </td>
                <td className="py-1 whitespace-nowrap">
                  {row.state !== "confirmed" && (
                    <Button
                      variant="outline"
                      disabled={busy}
                      onClick={() =>
                        onDecide({ key: row.multiplier_key, level: row.level, state: "confirmed" })
                      }
                    >
                      Confirm
                    </Button>
                  )}{" "}
                  {row.state !== "rejected" && (
                    <Button
                      variant="outline"
                      disabled={busy}
                      onClick={() =>
                        onDecide({ key: row.multiplier_key, level: row.level, state: "rejected" })
                      }
                    >
                      Reject
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <form
        className="flex flex-wrap items-center gap-2 text-sm"
        onSubmit={(event) => {
          event.preventDefault();
          onDecide({ key, level: level || null, state: "confirmed", basis });
          setKey("");
          setBasis("");
        }}
      >
        <select
          aria-label="Multiplier"
          className="rounded border px-1"
          value={key}
          onChange={(event) => setKey(event.target.value)}
        >
          <option value="">Add a condition…</option>
          {data.catalogue.multipliers.map((item) => (
            <option key={item.key} value={item.key}>
              {item.label} (× {Number(item.value).toFixed(2)})
            </option>
          ))}
        </select>
        <select
          aria-label="Applies to"
          className="rounded border px-1"
          value={level}
          onChange={(event) => setLevel(event.target.value)}
        >
          <option value="">the whole bid</option>
          {data.levels.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        <input
          aria-label="Why it applies"
          className="w-72 rounded border px-1"
          placeholder="Why it applies"
          value={basis}
          onChange={(event) => setBasis(event.target.value)}
        />
        <Button type="submit" variant="outline" disabled={busy || !key || !basis.trim()}>
          Confirm for the bid
        </Button>
      </form>
    </div>
  );
}

function TradeRate({ trade }: { trade: Trade }) {
  return (
    <details>
      <summary className="cursor-pointer text-sm">
        {trade.label}: SGD {Number(trade.hourly).toFixed(2)} an hour (
        {trade.crew.map((part) => `${Number(part.percent)}% ${part.grade}`).join(", ")})
      </summary>
      <table className="mt-1 w-full text-xs" aria-label={`Rate build-up for ${trade.label}`}>
        <thead className="text-left text-muted-foreground">
          <tr>
            <th className="py-1 pr-3">Component</th>
            {trade.grades.map((grade) => (
              <th key={grade.grade} className="py-1 pr-3 text-right">
                {grade.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {(trade.grades[0]?.components ?? []).map((component, index) => (
            <tr key={component.key} className="border-t">
              <td className="py-1 pr-3">{component.label}</td>
              {trade.grades.map((grade) => (
                <td
                  key={grade.grade}
                  className="py-1 pr-3 text-right"
                  title={grade.components[index]?.basis}
                >
                  {Number(grade.components[index]?.hourly ?? 0).toFixed(4)}
                </td>
              ))}
            </tr>
          ))}
          <tr className="border-t font-medium">
            <td className="py-1 pr-3">An hour</td>
            {trade.grades.map((grade) => (
              <td key={grade.grade} className="py-1 pr-3 text-right">
                {Number(grade.hourly).toFixed(4)}
              </td>
            ))}
          </tr>
        </tbody>
      </table>
    </details>
  );
}
