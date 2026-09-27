import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { api, apiErrorMessage } from "@/api/client";
import type { components } from "@/api/schema";
import { AuthorisedImage } from "@/components/AuthorisedImage";
import { Button } from "@/components/ui/button";

/**
 * The bid's symbols (FR-VIS-02): what each legend symbol is, and what is counted.
 *
 * The first thing on the page is what is not counted: every symbol nobody has confirmed,
 * with how many times it appears. Nothing unmapped is ever counted, so that list is what
 * stands between the drawings and a trustworthy take-off. Below it, each legend row shows
 * the consultant's symbol, what it says, the proposal (by rule, model or earlier tender) and
 * how sure it was, with Confirm, Correct and Reject.
 */

type LegendRow = components["schemas"]["LegendRowOut"];
type Mapping = components["schemas"]["MappingOut"];
type Counts = components["schemas"]["CountsOut"];
type Choice = components["schemas"]["ObjectTypeChoice"];

const STATUS: Record<string, { label: string; tone: string }> = {
  reused: { label: "Reused", tone: "text-emerald-700 dark:text-emerald-400" },
  confirmed: {
    label: "Confirmed",
    tone: "text-emerald-700 dark:text-emerald-400",
  },
  proposed: { label: "To confirm", tone: "text-amber-700 dark:text-amber-400" },
  awaiting_model: { label: "Being read", tone: "text-muted-foreground" },
  rejected: { label: "Rejected", tone: "text-red-700 dark:text-red-400" },
  "no legend": {
    label: "No legend entry",
    tone: "text-red-700 dark:text-red-400",
  },
};

function Status({ value }: { value: string }) {
  const shown = STATUS[value] ?? { label: value, tone: "" };
  return (
    <span className={`text-xs font-medium ${shown.tone}`}>{shown.label}</span>
  );
}

function sourceOf(mapping: Mapping): string {
  if (mapping.source === "rule") return "keyword rule";
  if (mapping.source === "model") {
    const sure =
      mapping.confidence == null
        ? ""
        : `, ${Math.round(mapping.confidence * 100)}% sure`;
    return `model${mapping.model ? ` ${mapping.model}` : ""}${sure}`;
  }
  if (mapping.source === "reuse") return "an earlier tender";
  return "a person";
}

export function SymbolsPage() {
  const { bidId = "" } = useParams();
  const legend = useQuery({
    queryKey: ["symbols", bidId, "legend"],
    queryFn: async (): Promise<LegendRow[]> => {
      const { data, error } = await api.GET("/bids/{bid_id}/symbols/legend", {
        params: { path: { bid_id: bidId } },
      });
      if (error || !data) throw new Error("Could not read the legends");
      return data;
    },
  });
  const counts = useQuery({
    queryKey: ["symbols", bidId, "counts"],
    queryFn: async (): Promise<Counts> => {
      const { data, error } = await api.GET("/bids/{bid_id}/symbols/counts", {
        params: { path: { bid_id: bidId } },
      });
      if (error || !data) throw new Error("Could not read the counts");
      return data;
    },
  });
  const choices = useQuery({
    queryKey: ["symbols", bidId, "choices"],
    queryFn: async (): Promise<Choice[]> => {
      const { data, error } = await api.GET(
        "/bids/{bid_id}/symbols/object-types",
        {
          params: { path: { bid_id: bidId } },
        },
      );
      if (error || !data) throw new Error("Could not read the object library");
      return data;
    },
  });

  return (
    <section className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Symbols</h1>
        <p className="text-sm text-muted-foreground">
          What each consultant symbol is. Only symbols a person has confirmed
          are counted.
        </p>
      </div>

      {counts.data && <CountsPanel counts={counts.data} />}

      <div className="space-y-2">
        <h2 className="text-lg font-medium">Legend</h2>
        {legend.isLoading && <p className="text-sm">Reading the legends…</p>}
        {legend.error && (
          <p className="text-sm text-red-700">{String(legend.error)}</p>
        )}
        {legend.data && legend.data.length === 0 && (
          <p className="text-sm text-muted-foreground">
            No legend has been found on this bid's drawings yet.
          </p>
        )}
        {legend.data && legend.data.length > 0 && (
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-muted-foreground">
              <tr>
                <th className="py-2 pr-3">Symbol</th>
                <th className="py-2 pr-3">Legend says</th>
                <th className="py-2 pr-3">Maps to</th>
                <th className="py-2 pr-3">Status</th>
                <th className="py-2" />
              </tr>
            </thead>
            <tbody>
              {legend.data.map((row) => (
                <LegendRowView
                  key={row.id}
                  bidId={bidId}
                  row={row}
                  choices={choices.data ?? []}
                />
              ))}
            </tbody>
          </table>
        )}
      </div>

      <Link to={`/bids/${bidId}`} className="inline-block text-sm underline">
        Back to the bid
      </Link>
    </section>
  );
}

function CountsPanel({ counts }: { counts: Counts }) {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <div className="rounded-md border p-4">
        <h2 className="mb-2 font-medium">Not counted</h2>
        {counts.unmapped.length === 0 ? (
          <p className="text-sm text-emerald-700 dark:text-emerald-400">
            Every symbol on the drawings is mapped.
          </p>
        ) : (
          <ul className="space-y-1 text-sm" aria-label="Unmapped symbols">
            {counts.unmapped.map((group) => (
              <li key={group.symbol_key} className="flex justify-between gap-3">
                <span>
                  {group.description ?? group.block ?? "Unexplained symbol"}{" "}
                  <Status value={group.status} />
                </span>
                <span className="tabular-nums">{group.instances}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
      <div className="rounded-md border p-4">
        <h2 className="mb-2 font-medium">Counted</h2>
        {counts.counted.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Nothing is confirmed yet.
          </p>
        ) : (
          <ul className="space-y-1 text-sm" aria-label="Counted objects">
            {counts.counted.map((item) => (
              <li key={item.object_type} className="flex justify-between gap-3">
                <span>{item.label}</span>
                <span className="tabular-nums">{item.count}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function LegendRowView({
  bidId,
  row,
  choices,
}: {
  bidId: string;
  row: LegendRow;
  choices: Choice[];
}) {
  const queryClient = useQueryClient();
  const mapping = row.mapping;
  const [correcting, setCorrecting] = useState(false);
  const [chosen, setChosen] = useState(mapping?.object_type ?? "");
  const decide = useMutation({
    mutationFn: async (decision: {
      action: "confirm" | "reject";
      objectType?: string;
    }) => {
      if (!mapping) return;
      const path = { bid_id: bidId, lineage_id: mapping.lineage_id };
      const { error } =
        decision.action === "confirm"
          ? await api.POST(
              "/bids/{bid_id}/symbols/mappings/{lineage_id}/confirm",
              {
                params: { path },
                body: decision.objectType
                  ? { object_type: decision.objectType }
                  : {},
              },
            )
          : await api.POST(
              "/bids/{bid_id}/symbols/mappings/{lineage_id}/reject",
              {
                params: { path },
                body: {},
              },
            );
      if (error)
        throw new Error(apiErrorMessage(error, "The decision was not saved"));
    },
    onSuccess: async () => {
      setCorrecting(false);
      await queryClient.invalidateQueries({ queryKey: ["symbols", bidId] });
    },
  });
  const label = (key: string | null | undefined) =>
    choices.find((choice) => choice.key === key)?.label ?? key ?? "not decided";
  const open = mapping && mapping.state !== "confirmed";

  return (
    <tr className="border-t align-top">
      <td className="py-2 pr-3">
        {row.has_crop ? (
          <AuthorisedImage
            path={`/api/bids/${bidId}/symbols/legend/${row.id}/crop.png`}
            alt={`Legend row: ${row.description}`}
            className="h-10 w-40 rounded border bg-white object-contain"
          />
        ) : null}
      </td>
      <td className="py-2 pr-3">{row.description}</td>
      <td className="py-2 pr-3">
        {mapping ? (
          <>
            <div>{label(mapping.object_type)}</div>
            <div className="text-xs text-muted-foreground">
              {mapping.state === "confirmed" && mapping.confirmed_by
                ? `confirmed by ${mapping.confirmed_by}`
                : `proposed by ${sourceOf(mapping)}`}
            </div>
            {mapping.reason && (
              <div className="text-xs text-muted-foreground">
                {mapping.reason}
              </div>
            )}
          </>
        ) : (
          <span className="text-muted-foreground">being read</span>
        )}
      </td>
      <td className="py-2 pr-3">
        <Status value={row.status} />
      </td>
      <td className="space-y-1 py-2 text-right">
        {open && !correcting && (
          <div className="flex justify-end gap-2">
            <Button
              size="sm"
              disabled={!mapping?.object_type || decide.isPending}
              onClick={() => decide.mutate({ action: "confirm" })}
            >
              Confirm
            </Button>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setCorrecting(true)}
            >
              Correct
            </Button>
            <Button
              size="sm"
              variant="outline"
              disabled={decide.isPending}
              onClick={() => decide.mutate({ action: "reject" })}
            >
              Reject
            </Button>
          </div>
        )}
        {mapping && correcting && (
          <div className="flex justify-end gap-2">
            <label className="sr-only" htmlFor={`type-${row.id}`}>
              Object type
            </label>
            <select
              id={`type-${row.id}`}
              value={chosen}
              onChange={(event) => setChosen(event.target.value)}
              className="rounded-md border bg-background px-2 py-1 text-sm"
            >
              <option value="">Choose a type…</option>
              {choices.map((choice) => (
                <option key={choice.key} value={choice.key}>
                  {choice.label}
                </option>
              ))}
            </select>
            <Button
              size="sm"
              disabled={!chosen || decide.isPending}
              onClick={() =>
                decide.mutate({ action: "confirm", objectType: chosen })
              }
            >
              Save
            </Button>
          </div>
        )}
        {mapping && mapping.state === "confirmed" && !correcting && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => setCorrecting(true)}
          >
            Change
          </Button>
        )}
        {decide.error && (
          <p className="text-xs text-red-700">{String(decide.error.message)}</p>
        )}
      </td>
    </tr>
  );
}
