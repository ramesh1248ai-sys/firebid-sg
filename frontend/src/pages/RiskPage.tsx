import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleCheck, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { useParams } from "react-router";

import { api, apiErrorMessage } from "@/api/client";
import type { components } from "@/api/schema";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

/**
 * Bid risk and qualifications (FR-RSK-01 to 06).
 *
 * The scope-gap checklist is pre-filled from the scope matrix and the takeoff; a person
 * resolves each item. Risks are found by rule with the evidence they rest on; each gets a
 * treatment, an owner and a status, and an impact the estimator accepts or adjusts with a
 * reason. Assumptions, exclusions, qualifications and deviations each link to the item they
 * come from. G3 waits until every checklist item is resolved and every risk is treated.
 */

type Page = components["schemas"]["RiskPageOut"];
type Check = components["schemas"]["CheckOut"];
type Risk = components["schemas"]["RiskOut"];
type Entry = components["schemas"]["QualificationEntryOut"];
type History = components["schemas"]["HistoryOut"];

interface Evidence {
  label?: string;
  quote?: string;
}

const words = (value: string) => value.replaceAll("_", " ");

export function RiskPage() {
  const { bidId = "" } = useParams();
  const client = useQueryClient();
  const path = { params: { path: { bid_id: bidId } } };
  const key = ["risk", bidId];
  const [error, setError] = useState<string | null>(null);
  const fail = (caught: unknown) =>
    setError(caught instanceof Error ? caught.message : "Something went wrong");
  const done = {
    onSuccess: (data: Page) => {
      setError(null);
      client.setQueryData(key, data);
    },
    onError: fail,
  };

  const page = useQuery({
    queryKey: key,
    queryFn: async (): Promise<Page> => {
      const { data, error: refused } = await api.GET("/bids/{bid_id}/risk", path);
      if (refused || !data) throw new Error("Could not load the risks");
      return data;
    },
  });
  const run = useMutation({
    mutationFn: async (what: "checklist/build" | "find" | "qualifications/propose") => {
      const { data, error: refused } = await api.POST(
        what === "find"
          ? "/bids/{bid_id}/risk/find"
          : what === "checklist/build"
            ? "/bids/{bid_id}/risk/checklist/build"
            : "/bids/{bid_id}/risk/qualifications/propose",
        path,
      );
      if (refused || !data) throw new Error(apiErrorMessage(refused, "Could not do that"));
      return data;
    },
    ...done,
  });
  const resolve = useMutation({
    mutationFn: async (input: { id: string; status: string; note: string }) => {
      const { data, error: refused } = await api.PUT("/bids/{bid_id}/risk/checklist/{check_id}", {
        params: { path: { bid_id: bidId, check_id: input.id } },
        body: { status: input.status, note: input.note || null },
      });
      if (refused || !data) throw new Error(apiErrorMessage(refused, "Could not resolve it"));
      return data;
    },
    ...done,
  });
  const treat = useMutation({
    mutationFn: async (input: { id: string; body: components["schemas"]["TreatIn"] }) => {
      const { data, error: refused } = await api.PUT("/bids/{bid_id}/risk/risks/{risk_id}", {
        params: { path: { bid_id: bidId, risk_id: input.id } },
        body: input.body,
      });
      if (refused || !data) throw new Error(apiErrorMessage(refused, "Could not save the risk"));
      return data;
    },
    ...done,
  });
  const impact = useMutation({
    mutationFn: async (input: { id: string; body: components["schemas"]["RiskImpactIn"] }) => {
      const { data, error: refused } = await api.PUT(
        "/bids/{bid_id}/risk/risks/{risk_id}/impact",
        { params: { path: { bid_id: bidId, risk_id: input.id } }, body: input.body },
      );
      if (refused || !data) throw new Error(apiErrorMessage(refused, "Could not save the impact"));
      return data;
    },
    ...done,
  });
  const edit = useMutation({
    mutationFn: async (input: { id: string; text: string }) => {
      const { data, error: refused } = await api.PATCH(
        "/bids/{bid_id}/risk/qualifications/{entry_id}",
        { params: { path: { bid_id: bidId, entry_id: input.id } }, body: { text: input.text } },
      );
      if (refused || !data) throw new Error(apiErrorMessage(refused, "Could not save the entry"));
      return data;
    },
    ...done,
  });
  const decide = useMutation({
    mutationFn: async (input: { id: string; decision: string }) => {
      const { error: refused } = await api.POST(
        "/bids/{bid_id}/qualifications/{qualification_id}/decide",
        {
          params: { path: { bid_id: bidId, qualification_id: input.id } },
          body: { decision: input.decision },
        },
      );
      if (refused) throw new Error(apiErrorMessage(refused, "Could not record it"));
    },
    onSuccess: () => {
      setError(null);
      void client.invalidateQueries({ queryKey: key });
    },
    onError: fail,
  });

  if (page.isPending) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Loading the risks…
      </p>
    );
  }
  if (page.isError) {
    return (
      <p role="alert" className="text-sm text-destructive">
        Could not load the risks
      </p>
    );
  }
  const data = page.data;
  const busy = run.isPending || resolve.isPending || treat.isPending || impact.isPending;
  const systems = [...new Set(data.checklist.map((row) => row.system))];

  return (
    <section className="space-y-8">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Risk and qualifications</h1>
        <p className="text-sm text-muted-foreground">
          Scope gaps, design responsibility and execution risk, each with its evidence, and the
          assumptions, exclusions and qualifications the offer is made on.
        </p>
      </div>

      <p
        role="status"
        aria-label="G3 readiness"
        className={`flex items-start gap-2 rounded-lg border px-4 py-3 text-sm ${
          data.g3.ready
            ? "border-emerald-200 bg-emerald-50 text-emerald-800"
            : "border-amber-200 bg-amber-50 text-amber-800"
        }`}
      >
        {data.g3.ready ? (
          <CircleCheck className="mt-0.5 size-4 shrink-0" aria-hidden />
        ) : (
          <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
        )}
        <span>
          {data.g3.ready
            ? "Ready for G3: every checklist item is resolved and every risk has a treatment."
            : `Not ready for G3: ${data.g3.summary}.`}
        </span>
      </p>

      {error && (
        <p
          role="alert"
          className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
        >
          {error}
        </p>
      )}

      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-medium">Scope-gap checklist</h2>
          <Button variant="outline" disabled={busy} onClick={() => run.mutate("checklist/build")}>
            {data.checklist.length ? "Refresh from the scope matrix" : "Build the checklist"}
          </Button>
        </div>
        {data.checklist.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Not built. It is pre-filled from the scope matrix and the takeoff.
          </p>
        ) : (
          systems.map((system) => (
            <table key={system} className="w-full text-sm" aria-label={`Checklist: ${words(system)}`}>
              <caption className="py-1 text-left font-medium capitalize">{words(system)}</caption>
              <thead className="text-left">
                <tr>
                  <th className="py-2 pr-3">Item</th>
                  <th className="py-2 pr-3">Proposed</th>
                  <th className="py-2 pr-3">Status</th>
                  <th className="py-2" />
                </tr>
              </thead>
              <tbody>
                {data.checklist
                  .filter((row) => row.system === system)
                  .map((row) => (
                    <CheckRow
                      key={row.id}
                      row={row}
                      statuses={data.statuses}
                      busy={busy}
                      onResolve={(status, note) => resolve.mutate({ id: row.id, status, note })}
                    />
                  ))}
              </tbody>
            </table>
          ))
        )}
      </div>

      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-medium">Risk register</h2>
          <Button variant="outline" disabled={busy} onClick={() => run.mutate("find")}>
            Find the risks
          </Button>
        </div>
        {data.risks.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            None found yet. Risks are read from the specification, the drawings and the bid's
            parameters.
          </p>
        ) : (
          <ul aria-label="Risk register" className="space-y-3">
            {data.risks.map((risk) => (
              <RiskCard
                key={risk.id}
                risk={risk}
                treatments={data.treatments}
                busy={busy}
                onTreat={(body) => treat.mutate({ id: risk.id, body })}
                onImpact={(body) => impact.mutate({ id: risk.id, body })}
              />
            ))}
          </ul>
        )}
      </div>

      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-medium">
            Assumptions, exclusions and qualifications{" "}
            <span className="text-sm font-normal text-muted-foreground">
              each linked to the item it comes from
            </span>
          </h2>
          <Button
            variant="outline"
            disabled={busy}
            onClick={() => run.mutate("qualifications/propose")}
          >
            Propose from risks and scope
          </Button>
        </div>
        {data.qualifications.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing is proposed.</p>
        ) : (
          <ul aria-label="Qualifications" className="divide-y rounded-lg border">
            {data.qualifications.map((entry) => (
              <EntryRow
                key={entry.id}
                bidId={bidId}
                entry={entry}
                onEdit={(text) => edit.mutate({ id: entry.id, text })}
                onDecide={(decision) => decide.mutate({ id: entry.id, decision })}
              />
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function CheckRow({
  row,
  statuses,
  busy,
  onResolve,
}: {
  row: Check;
  statuses: string[];
  busy: boolean;
  onResolve: (status: string, note: string) => void;
}) {
  const [status, setStatus] = useState(row.status === "open" ? "" : row.status);
  const [note, setNote] = useState(row.note ?? "");
  return (
    <tr className="border-t align-top" data-status={row.status}>
      <td className="py-2 pr-3">{row.label}</td>
      <td className="py-2 pr-3 text-xs">
        <span className={row.proposed_status === "open" ? "font-medium text-amber-700" : ""}>
          {row.proposed_status === "open" ? "not settled" : words(row.proposed_status)}
        </span>
        <div className="text-muted-foreground">{row.basis}</div>
      </td>
      <td className="py-2 pr-3 text-xs">
        <Badge tone={row.status === "open" ? "warn" : "good"}>
          {row.status === "open" ? "to resolve" : words(row.status)}
        </Badge>
        {row.decided_by && <div className="mt-1 text-muted-foreground">by {row.decided_by}</div>}
      </td>
      <td className="py-2">
        <div className="flex flex-wrap items-center justify-end gap-2">
          <select
            aria-label={`Status of ${row.label}`}
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
            <option value="">Resolve as…</option>
            {statuses.map((item) => (
              <option key={item} value={item}>
                {words(item)}
              </option>
            ))}
          </select>
          <input
            aria-label={`Note on ${row.label}`}
            className="w-44"
            placeholder="Why"
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
          <Button
            variant="outline"
            size="sm"
            disabled={busy || !status}
            aria-label={`Resolve ${row.label}`}
            onClick={() => onResolve(status, note)}
          >
            Save
          </Button>
        </div>
      </td>
    </tr>
  );
}

function RiskCard({
  risk,
  treatments,
  busy,
  onTreat,
  onImpact,
}: {
  risk: Risk;
  treatments: string[];
  busy: boolean;
  onTreat: (body: components["schemas"]["TreatIn"]) => void;
  onImpact: (body: components["schemas"]["RiskImpactIn"]) => void;
}) {
  const [treatment, setTreatment] = useState(risk.treatment ?? risk.proposed_treatment);
  const [owner, setOwner] = useState(risk.owner ?? "");
  const [status, setStatus] = useState(risk.status);
  const [cost, setCost] = useState("");
  const [hours, setHours] = useState("");
  const [reason, setReason] = useState("");
  const computed = risk.impact as { method?: string; basis?: string };
  return (
    <li className="space-y-3 rounded-lg border p-4" data-treated={risk.treatment ? "yes" : "no"}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="font-medium">{risk.title}</div>
          <div className="text-sm text-muted-foreground">{risk.description}</div>
        </div>
        <div className="flex gap-1">
          <Badge tone="info">{words(risk.category)}</Badge>
          <Badge tone={risk.treatment ? "good" : "warn"}>
            {risk.treatment ? `${risk.treatment} · ${risk.status}` : "no treatment"}
          </Badge>
        </div>
      </div>
      <ul aria-label={`Evidence for ${risk.title}`} className="space-y-1 text-xs">
        {(risk.evidence as Evidence[]).map((item) => (
          <li key={`${item.label}:${item.quote}`}>
            <span className="font-medium">{item.label}</span>
            {item.quote ? <span className="text-muted-foreground"> · “{item.quote}”</span> : null}
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <select
          aria-label={`Treatment of ${risk.title}`}
          value={treatment}
          onChange={(event) => setTreatment(event.target.value)}
        >
          {treatments.map((item) => (
            <option key={item} value={item}>
              {item}
              {item === risk.proposed_treatment ? " (proposed)" : ""}
            </option>
          ))}
        </select>
        <input
          aria-label={`Owner of ${risk.title}`}
          className="w-40"
          placeholder="Owner"
          value={owner}
          onChange={(event) => setOwner(event.target.value)}
        />
        <select
          aria-label={`Status of ${risk.title}`}
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        >
          <option value="open">open</option>
          <option value="treated">treated</option>
          <option value="closed">closed</option>
        </select>
        <Button
          variant="outline"
          size="sm"
          disabled={busy}
          aria-label={`Save the treatment of ${risk.title}`}
          onClick={() =>
            onTreat({
              treatment,
              owner,
              // A first treatment marks the risk treated; the status is sent once it has one.
              status: risk.treatment ? status : null,
            })
          }
        >
          Save
        </Button>
      </div>
      <div className="space-y-2 rounded-md bg-muted/50 p-3 text-sm">
        <div>
          <span className="font-medium">Impact: </span>
          {risk.cost_allowance != null ? `SGD ${risk.cost_allowance}` : "no allowance"}
          {risk.programme_hours != null ? ` · ${Number(risk.programme_hours)} man-hours` : ""}{" "}
          <Badge tone={risk.impact_state === "computed" ? "neutral" : "good"}>
            {risk.impact_state}
            {risk.impact_by ? ` by ${risk.impact_by}` : ""}
          </Badge>
        </div>
        <div className="text-xs text-muted-foreground">
          {computed.method === "not_computed" ? "Not computed: " : "Computed: "}
          {computed.basis}
        </div>
        {risk.impact_reason && <div className="text-xs">Reason: {risk.impact_reason}</div>}
        <div className="flex flex-wrap items-center gap-2">
          {computed.method !== "not_computed" && risk.impact_state === "computed" && (
            <Button
              variant="outline"
              size="sm"
              disabled={busy}
              onClick={() => onImpact({ accept_computed: true })}
            >
              Accept the computed impact
            </Button>
          )}
          <input
            aria-label={`Allowance for ${risk.title}`}
            className="w-28"
            inputMode="decimal"
            placeholder="SGD"
            value={cost}
            onChange={(event) => setCost(event.target.value)}
          />
          <input
            aria-label={`Man-hours for ${risk.title}`}
            className="w-28"
            inputMode="decimal"
            placeholder="man-hours"
            value={hours}
            onChange={(event) => setHours(event.target.value)}
          />
          <input
            aria-label={`Reason for ${risk.title}`}
            className="w-64"
            placeholder="Why this figure"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
          <Button
            variant="outline"
            size="sm"
            disabled={busy || !reason.trim()}
            aria-label={`Adjust the impact of ${risk.title}`}
            onClick={() =>
              onImpact({ cost: cost || null, hours: hours || null, reason, accept_computed: false })
            }
          >
            Adjust
          </Button>
        </div>
      </div>
    </li>
  );
}

function EntryRow({
  bidId,
  entry,
  onEdit,
  onDecide,
}: {
  bidId: string;
  entry: Entry;
  onEdit: (text: string) => void;
  onDecide: (decision: string) => void;
}) {
  const [text, setText] = useState(entry.text);
  const [showing, setShowing] = useState(false);
  const history = useQuery({
    queryKey: ["risk", bidId, "history", entry.id],
    enabled: showing,
    queryFn: async (): Promise<History[]> => {
      const { data } = await api.GET("/bids/{bid_id}/risk/qualifications/{entry_id}/history", {
        params: { path: { bid_id: bidId, entry_id: entry.id } },
      });
      return data ?? [];
    },
  });
  return (
    <li className="space-y-2 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={entry.kind === "assumption" ? "info" : "warn"}>{entry.kind}</Badge>
        <span className="text-xs text-muted-foreground">
          from {words(entry.source_kind)}: {entry.source_label}
        </span>
        <span className="text-xs text-muted-foreground">
          · {entry.state}
          {entry.decided_by ? ` by ${entry.decided_by}` : ""}
        </span>
      </div>
      <textarea
        aria-label={`Wording of the ${entry.kind} from ${entry.source_label}`}
        className="block min-h-16 w-full"
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          size="sm"
          disabled={text.trim() === entry.text || !text.trim()}
          onClick={() => onEdit(text)}
        >
          Save the wording
        </Button>
        {entry.state === "proposed" && (
          <>
            <Button variant="outline" size="sm" onClick={() => onDecide("accepted")}>
              Accept
            </Button>
            <Button variant="outline" size="sm" onClick={() => onDecide("rejected")}>
              Reject
            </Button>
          </>
        )}
        <Button variant="ghost" size="sm" onClick={() => setShowing(!showing)}>
          {showing ? "Hide the history" : "History"}
        </Button>
      </div>
      {showing && (
        <ol aria-label="History" className="space-y-1 text-xs text-muted-foreground">
          {(history.data ?? []).length === 0 && <li>No changes since it was proposed.</li>}
          {(history.data ?? []).map((item) => (
            <li key={`${item.at}:${item.action}`}>
              {new Date(item.at).toLocaleString("en-SG")} · {item.by} · {item.action}
              {item.reason ? ` · ${item.reason}` : ""}
            </li>
          ))}
        </ol>
      )}
    </li>
  );
}
