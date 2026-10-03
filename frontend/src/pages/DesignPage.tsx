import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Fragment, useState } from "react";
import { Link, useParams } from "react-router";

import { api, apiErrorMessage } from "@/api/client";
import type { components } from "@/api/schema";
import { Button } from "@/components/ui/button";

/**
 * Design development (FR-DSN-01 to 04): for a tender drawn as design intent.
 *
 * Such a tender shows the mains and leaves the heads and range pipes to the contractor, so a
 * takeoff of what is drawn counts no heads. This page lists each plan sheet with the design
 * criteria its notes state. A senior estimator or design manager confirms the criterion; only
 * then does the platform propose a layout. The proposal appears on the workbench like any
 * detection, and the takeoff keeps it as items of its own, marked "proposed layout, not drawn".
 *
 * It is an estimating aid, not a design for approval.
 */

type Design = components["schemas"]["DesignOut"];
type Sheet = components["schemas"]["SheetDesignOut"];
type Criterion = components["schemas"]["CriterionOut"];

const STATE_LABEL: Record<string, string> = {
  proposed: "Awaiting confirmation",
  confirmed: "Confirmed",
  blocked: "Cannot be laid out",
};

function describeCriterion(criterion: Criterion): string {
  const [along = 0, across = 0] = criterion.max_spacing_mm;
  return `${criterion.title}: ${along / 1000} m × ${across / 1000} m, ${criterion.max_area_m2} m² a head`;
}

/** A design-intent sheet with no heads drawn, not yet confirmed: what a person would pick. */
function suggested(sheet: Sheet): boolean {
  return sheet.state === "proposed" && sheet.design_intent && sheet.drawn_heads === 0;
}

export function DesignPage() {
  const { bidId = "" } = useParams();
  const queryClient = useQueryClient();
  const [picked, setPicked] = useState<Set<string> | null>(null);
  const [key, setKey] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const design = useQuery({
    queryKey: ["design", bidId],
    queryFn: async (): Promise<Design> => {
      const { data } = await api.GET("/bids/{bid_id}/design", { params: { path: { bid_id: bidId } } });
      if (!data) throw new Error("Could not read the design basis");
      return data;
    },
  });
  const sheets = design.data?.sheets ?? [];
  const selected = picked ?? new Set(sheets.filter(suggested).map((sheet) => sheet.sheet_id));
  // The criteria the selected sheets state, by key: each sheet uses its own notes' wording.
  const choices = new Map<string, Criterion>();
  for (const sheet of sheets) {
    if (!selected.has(sheet.sheet_id)) continue;
    for (const criterion of sheet.criteria) {
      if (!choices.has(criterion.key)) choices.set(criterion.key, criterion);
    }
  }
  const chosen = choices.has(key) ? key : (choices.keys().next().value ?? "");

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["design", bidId] });
  const read = useMutation({
    mutationFn: async () => {
      const { error } = await api.POST("/bids/{bid_id}/design/basis", {
        params: { path: { bid_id: bidId } },
      });
      if (error) throw new Error(apiErrorMessage(error, "Could not queue the reading"));
    },
    onSuccess: () => {
      setMessage("Reading the plan sheets' notes. Refresh in a moment.");
      void refresh();
    },
    onError: (error: Error) => setMessage(error.message),
  });
  const confirm = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST("/bids/{bid_id}/design/confirm", {
        params: { path: { bid_id: bidId } },
        body: { sheet_ids: [...selected], key: chosen },
      });
      if (error || !data) throw new Error(apiErrorMessage(error, "Could not confirm the design basis"));
      return data;
    },
    onSuccess: (data) => {
      const skipped = Object.keys(data.skipped).length;
      setMessage(
        `${data.confirmed.length} sheet${data.confirmed.length === 1 ? "" : "s"} confirmed; the layout is being proposed.` +
          (skipped ? ` ${skipped} left as they were: they do not state this criterion, or cannot be laid out.` : ""),
      );
      setPicked(new Set());
      void refresh();
    },
    onError: (error: Error) => setMessage(error.message),
  });
  const withdraw = useMutation({
    mutationFn: async (sheetId: string) => {
      const { error } = await api.POST("/bids/{bid_id}/design/sheets/{sheet_id}/withdraw", {
        params: { path: { bid_id: bidId, sheet_id: sheetId } },
        body: {},
      });
      if (error) throw new Error(apiErrorMessage(error, "Could not withdraw the layout"));
    },
    onSuccess: () => {
      setMessage("The proposed layout was removed from this sheet.");
      void refresh();
    },
    onError: (error: Error) => setMessage(error.message),
  });

  const toggle = (sheetId: string) => {
    const next = new Set(selected);
    if (next.has(sheetId)) next.delete(sheetId);
    else next.add(sheetId);
    setPicked(next);
  };
  const heads = sheets.reduce((sum, sheet) => sum + Number(sheet.totals.heads ?? 0), 0);

  return (
    <section className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Design development</h1>
        <p className="text-sm text-muted-foreground">
          For sheets drawn as design intent, where the heads and range pipes are the contractor's to
          develop. Confirm the design criterion for each sheet and the platform proposes a layout to
          take off from. A proposal is an estimating aid, not a design for approval.
        </p>
      </div>
      {design.data && (
        <p role="note" className="rounded-md border p-3 text-sm">
          Design rules version {design.data.rule_version} ({design.data.rule_status}).
          {design.data.rule_status !== "confirmed" &&
            " Their spacing, omissions and pipe sizing are seeded defaults: a senior estimator should confirm them before these quantities are relied on."}
        </p>
      )}
      <div className="flex flex-wrap items-end gap-3">
        <Button variant="outline" onClick={() => read.mutate()} disabled={read.isPending}>
          Read the design basis
        </Button>
        <label className="text-sm">
          <span className="mb-1 block text-xs text-muted-foreground">Criterion for the selected sheets</span>
          <select
            className="rounded-md border bg-background px-2 py-2 text-sm"
            value={chosen}
            onChange={(event) => setKey(event.target.value)}
            disabled={choices.size === 0}
          >
            {[...choices.values()].map((criterion) => (
              <option key={criterion.key} value={criterion.key}>
                {describeCriterion(criterion)}
              </option>
            ))}
          </select>
        </label>
        <Button onClick={() => confirm.mutate()} disabled={selected.size === 0 || !chosen || confirm.isPending}>
          Confirm and propose a layout ({selected.size})
        </Button>
      </div>
      {message && (
        <p role="status" className="text-sm">
          {message}
        </p>
      )}
      {design.isLoading && <p className="text-sm">Reading…</p>}
      {design.data && sheets.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No design basis has been read yet. Read it once the drawings are parsed and their scales verified.
        </p>
      )}
      {sheets.length > 0 && (
        <table className="w-full text-sm" aria-label="Plan sheets">
          <thead className="text-left text-xs text-muted-foreground">
            <tr>
              <th className="py-2 pr-3" />
              <th className="py-2 pr-3">Sheet</th>
              <th className="py-2 pr-3">Level</th>
              <th className="py-2 pr-3">Status</th>
              <th className="py-2 pr-3">Criterion</th>
              <th className="py-2 pr-3 text-right">Heads drawn</th>
              <th className="py-2 pr-3 text-right">Heads proposed</th>
              <th className="py-2 pr-3 text-right">Range pipe</th>
            </tr>
          </thead>
          <tbody>
            {sheets.map((sheet) => (
              <Fragment key={sheet.sheet_id}>
                <SheetRow
                  sheet={sheet}
                  bidId={bidId}
                  selected={selected.has(sheet.sheet_id)}
                  onSelect={() => toggle(sheet.sheet_id)}
                  open={open === sheet.sheet_id}
                  onToggle={() => setOpen(open === sheet.sheet_id ? null : sheet.sheet_id)}
                />
                {open === sheet.sheet_id && (
                  <Detail sheet={sheet} onWithdraw={() => withdraw.mutate(sheet.sheet_id)} busy={withdraw.isPending} />
                )}
              </Fragment>
            ))}
          </tbody>
          <tfoot>
            <tr className="border-t font-medium">
              <td colSpan={6} className="py-2 pr-3">
                Proposed across the bid, before duplicates between sheets are resolved
              </td>
              <td className="py-2 pr-3 text-right">{heads}</td>
              <td />
            </tr>
          </tfoot>
        </table>
      )}
    </section>
  );
}

function pipeTotal(sheet: Sheet): string {
  const lengths = (sheet.totals.range_pipe_m ?? {}) as Record<string, number>;
  const total = Object.values(lengths).reduce((sum, metres) => sum + Number(metres), 0);
  return total ? `${total.toFixed(1)} m` : "–";
}

function SheetRow({
  sheet,
  bidId,
  selected,
  onSelect,
  open,
  onToggle,
}: {
  sheet: Sheet;
  bidId: string;
  selected: boolean;
  onSelect: () => void;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <tr className="border-t align-top">
      <td className="py-2 pr-3">
        <input
          type="checkbox"
          aria-label={`Select ${sheet.sheet_number}`}
          checked={selected}
          onChange={onSelect}
          disabled={sheet.state === "blocked"}
        />
      </td>
      <td className="py-2 pr-3">
        <button type="button" className="underline" onClick={onToggle} aria-expanded={open}>
          {sheet.sheet_number}
        </button>{" "}
        <Link to={`/bids/${bidId}/workbench?sheet=${sheet.sheet_id}`} className="text-xs text-muted-foreground underline">
          open
        </Link>
        {sheet.design_intent && <div className="text-xs text-muted-foreground">Design intent stated</div>}
      </td>
      <td className="py-2 pr-3">{sheet.level ?? "–"}</td>
      <td className={`py-2 pr-3 ${sheet.state === "blocked" ? "text-amber-700" : ""}`}>
        {STATE_LABEL[sheet.state] ?? sheet.state}
        {sheet.note && <div className="text-xs">{sheet.note}</div>}
        {sheet.confirmed_by && <div className="text-xs text-muted-foreground">by {sheet.confirmed_by}</div>}
      </td>
      <td className="py-2 pr-3">
        {sheet.criterion ? describeCriterion(sheet.criterion) : `${sheet.criteria.length} to choose from`}
      </td>
      <td className={`py-2 pr-3 text-right ${sheet.drawn_heads > 0 ? "text-amber-700" : ""}`}>
        {sheet.drawn_heads}
        {sheet.drawn_heads > 0 && <div className="text-xs">a layout would count these twice</div>}
      </td>
      <td className="py-2 pr-3 text-right">{sheet.state === "confirmed" ? String(sheet.totals.heads ?? "…") : "–"}</td>
      <td className="py-2 pr-3 text-right">{sheet.state === "confirmed" ? pipeTotal(sheet) : "–"}</td>
    </tr>
  );
}

function Detail({ sheet, onWithdraw, busy }: { sheet: Sheet; onWithdraw: () => void; busy: boolean }) {
  const omitted = sheet.spaces.filter((space) => space.omitted_by);
  const remote = Number(sheet.totals.remote_rows ?? 0);
  return (
    <tr>
      <td />
      <td colSpan={7} className="space-y-3 pb-4 text-xs">
        {sheet.intent_quote && <p className="text-muted-foreground">“{sheet.intent_quote}”</p>}
        <div>
          <p className="font-medium">Criteria this sheet states</p>
          <ul className="list-disc pl-5">
            {sheet.criteria.map((criterion) => (
              <li key={criterion.key}>
                {describeCriterion(criterion)}
                <span className="text-muted-foreground"> — {criterion.source}</span>
              </li>
            ))}
          </ul>
        </div>
        {sheet.state === "confirmed" && (
          <>
            <p>
              {sheet.spaces.length} spaces, {String(sheet.totals.area_m2 ?? "–")} m² of floor; design rules version{" "}
              {sheet.rule_version}.
              {remote > 0 &&
                ` ${remote} row${remote === 1 ? " is" : "s are"} fed by an allowance: no drawn main is within reach.`}
            </p>
            <div>
              <p className="font-medium">Left without heads ({omitted.length})</p>
              {omitted.length === 0 ? (
                <p className="text-muted-foreground">No space was omitted.</p>
              ) : (
                <ul className="list-disc pl-5" aria-label="Omitted spaces">
                  {omitted.map((space) => (
                    <li key={space.index}>
                      {space.name || "unnamed space"}, {space.area_m2} m²
                      <span className="text-muted-foreground"> — omission rule {space.omitted_by}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <Button variant="outline" size="sm" onClick={onWithdraw} disabled={busy}>
              Withdraw this sheet's layout
            </Button>
          </>
        )}
      </td>
    </tr>
  );
}
