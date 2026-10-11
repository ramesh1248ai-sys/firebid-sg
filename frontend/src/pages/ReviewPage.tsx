import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleCheck, Download, Lock, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { api, apiErrorMessage } from "@/api/client";
import type { components } from "@/api/schema";
import { accessToken } from "@/auth/oidc";
import { useAuth } from "@/auth/session";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

/**
 * Review and submission (FR-PKG-01, 02, 03; FR-LRN-02).
 *
 * The review pack is the estimate as an approver needs to see it, each section one click
 * from where it comes from. The gates say who approved what, on which hash, or what stands
 * in the way. The Senior Estimator approves G2 here and the Commercial Director G3 and G4,
 * each only their own. G4 freezes the submission: the page shows that it still verifies, and offers
 * its files for download. The platform sends nothing. Afterwards the outcome is recorded.
 */

type Pack = components["schemas"]["PackOut"];
type Gates = components["schemas"]["GatesOut"];
type Gate = components["schemas"]["GateOut"];
type Snapshot = components["schemas"]["SnapshotOut"];
type Outcome = components["schemas"]["OutcomeOut"];

const HEADLINE = [
  "total_excluding_gst",
  "gst",
  "total_including_gst",
  "margin",
  "risk_allowances",
  "unpriced_lines",
];

async function save(url: string, fallback: string) {
  const token = await accessToken();
  const response = await fetch(new URL(url, window.location.origin), {
    headers: token ? { authorization: `Bearer ${token}` } : {},
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(apiErrorMessage(body, "The download did not work"));
  }
  const disposition = response.headers.get("content-disposition") ?? "";
  const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? fallback;
  const blob = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = blob;
  link.download = name;
  link.click();
  URL.revokeObjectURL(blob);
}

export function ReviewPage() {
  const { bidId = "" } = useParams();
  const { session } = useAuth();
  const client = useQueryClient();
  const path = { params: { path: { bid_id: bidId } } };
  const [error, setError] = useState<string | null>(null);
  const [comment, setComment] = useState("");
  const fail = (caught: unknown) =>
    setError(caught instanceof Error ? caught.message : "Something went wrong");
  const refresh = () => {
    setError(null);
    void client.invalidateQueries({ queryKey: ["review", bidId] });
  };

  const pack = useQuery({
    queryKey: ["review", bidId, "pack"],
    queryFn: async (): Promise<Pack | null> => {
      const { data } = await api.GET("/bids/{bid_id}/review-pack", path);
      return data ?? null;
    },
  });
  const gates = useQuery({
    queryKey: ["review", bidId, "gates"],
    queryFn: async (): Promise<Gates | null> => {
      const { data } = await api.GET("/bids/{bid_id}/gates", path);
      return data ?? null;
    },
  });
  const snapshot = useQuery({
    queryKey: ["review", bidId, "submission"],
    queryFn: async (): Promise<Snapshot | null> => {
      const { data } = await api.GET("/bids/{bid_id}/submission", path);
      return data ?? null;
    },
  });
  const outcome = useQuery({
    queryKey: ["review", bidId, "outcome"],
    queryFn: async (): Promise<Outcome | null> => {
      const { data } = await api.GET("/bids/{bid_id}/outcome", path);
      return data ?? null;
    },
  });
  const approve = useMutation({
    mutationFn: async (gate: "g2" | "g3" | "g4") => {
      const { data, error: refused } = await api.POST(
        gate === "g2"
          ? "/bids/{bid_id}/boq/g2/approve"
          : gate === "g3"
            ? "/bids/{bid_id}/gates/g3/approve"
            : "/bids/{bid_id}/gates/g4/approve",
        { ...path, body: { comment: comment || null } },
      );
      if (refused || !data) throw new Error(apiErrorMessage(refused, "The gate was not approved"));
      return data;
    },
    onSuccess: () => {
      setComment("");
      refresh();
      // G3 and G4 move the bid, and G2 is shown on the BOQ page too.
      void client.invalidateQueries({ queryKey: ["bid", bidId] });
      void client.invalidateQueries({ queryKey: ["boq", bidId] });
    },
    onError: fail,
  });

  const isDirector = session?.roles.includes("commercial_director") ?? false;
  const isSenior = session?.roles.includes("senior_estimator") ?? false;
  // Each approver is offered their own gates and no other.
  const mine = [
    ...(isSenior ? (["G2"] as const) : []),
    ...(isDirector ? (["G3", "G4"] as const) : []),
  ];
  const data = pack.data;
  if (pack.isPending || gates.isPending) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Loading the review pack…
      </p>
    );
  }

  return (
    <section className="space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Review and submission</h1>
          <p className="text-sm text-muted-foreground">
            The estimate as an approver sees it, the gates, and what was submitted.
          </p>
        </div>
        <div className="flex gap-2">
          {(["pdf", "xlsx"] as const).map((format) => (
            <Button
              key={format}
              variant="outline"
              onClick={() =>
                save(
                  `/api/bids/${bidId}/review-pack/download?format=${format}`,
                  `review pack.${format}`,
                ).catch(fail)
              }
            >
              <Download aria-hidden />
              Review pack ({format === "pdf" ? "PDF" : "Excel"})
            </Button>
          ))}
        </div>
      </div>

      {error && (
        <p
          role="alert"
          className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800"
        >
          {error}
        </p>
      )}

      <div className="space-y-2">
        <h2 className="text-lg font-medium">Gates</h2>
        <ul aria-label="Gates" className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
          {(gates.data?.gates ?? []).map((gate) => (
            <GateCard key={gate.gate} gate={gate} />
          ))}
        </ul>
        {mine.length > 0 && gates.data && (
          <div className="flex flex-wrap items-center gap-2 rounded-lg border p-3">
            <input
              aria-label="Approval comment"
              className="w-80"
              placeholder="Comment for the record (optional)"
              value={comment}
              onChange={(event) => setComment(event.target.value)}
            />
            {mine.map((name) => {
              const gate = gates.data?.gates.find((item) => item.gate === name);
              if (!gate || gate.approved) return null;
              return (
                <Button
                  key={name}
                  disabled={approve.isPending || gate.blockers.length > 0}
                  onClick={() => approve.mutate(name === "G2" ? "g2" : name === "G3" ? "g3" : "g4")}
                >
                  {name === "G4" ? "Approve G4 and freeze the submission" : `Approve ${name}`}
                </Button>
              );
            })}
            {isDirector && (
              <span className="text-xs text-muted-foreground">
                G4 freezes the submission. The platform sends nothing: the files are then offered
                for download.
              </span>
            )}
          </div>
        )}
      </div>

      {snapshot.data && (
        <div className="space-y-2">
          <h2 className="text-lg font-medium">Frozen submission</h2>
          <p
            role="status"
            aria-label="Snapshot verification"
            className={`flex items-start gap-2 rounded-lg border px-4 py-3 text-sm ${
              snapshot.data.verified
                ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                : "border-red-200 bg-red-50 text-red-800"
            }`}
          >
            <Lock className="mt-0.5 size-4 shrink-0" aria-hidden />
            <span>
              {snapshot.data.verified
                ? "The snapshot verifies: the manifest and every file are as they were frozen."
                : `The snapshot does not verify: ${snapshot.data.problems.join("; ")}.`}{" "}
              Frozen by {snapshot.data.frozen_by} on{" "}
              {new Date(snapshot.data.frozen_at).toLocaleString("en-SG")}. Manifest{" "}
              <code className="text-xs">{snapshot.data.manifest_sha256.slice(0, 16)}…</code>
              {snapshot.data.changed_since.length > 0 &&
                ` ${snapshot.data.changed_since.length} record(s) have changed in the platform since.`}
            </span>
          </p>
          <ul aria-label="Submission files" className="divide-y rounded-lg border text-sm">
            {snapshot.data.files.map((file) => (
              <li key={file.name} className="flex items-center justify-between gap-3 p-3">
                <span>
                  {file.name}{" "}
                  <span className="text-xs text-muted-foreground">
                    {Math.ceil(file.size / 1024)} KB · {file.sha256.slice(0, 12)}…
                  </span>
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  aria-label={`Download ${file.name}`}
                  onClick={() =>
                    save(`/api/bids/${bidId}/submission/files/${file.name}`, file.name).catch(fail)
                  }
                >
                  <Download aria-hidden />
                  Download
                </Button>
              </li>
            ))}
          </ul>
        </div>
      )}

      {(snapshot.data || outcome.data) && (
        <OutcomePanel bidId={bidId} outcome={outcome.data ?? null} onSaved={refresh} onError={fail} />
      )}

      {data && (
        <div className="space-y-4">
          <h2 className="text-lg font-medium">
            Review pack{" "}
            <span className="text-sm font-normal text-muted-foreground">
              priced on {data.priced_on}
            </span>
          </h2>
          <dl aria-label="Headline figures" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {HEADLINE.map((key) => (
              <div key={key} className="rounded-lg border bg-muted/40 px-4 py-3">
                <dt className="text-xs text-muted-foreground">{data.figure_labels[key]}</dt>
                <dd className="text-lg font-semibold tabular-nums">
                  {data.figures[key] === "" ? "not set" : data.figures[key]}
                </dd>
              </div>
            ))}
          </dl>
          {data.sections.map((section) => (
            <div key={section.key} className="space-y-1">
              <div className="flex flex-wrap items-baseline justify-between gap-2">
                <h3 className="font-medium">{section.title}</h3>
                <Link to={section.link} className="text-sm">
                  Open in the platform
                </Link>
              </div>
              {section.note && <p className="text-xs text-muted-foreground">{section.note}</p>}
              {section.rows.length > 0 && (
                <table className="w-full text-sm" aria-label={section.title}>
                  <thead className="text-left">
                    <tr>
                      {section.columns.map((column) => (
                        <th key={column} className="py-2 pr-3">
                          {column}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {section.rows.map((row, index) => (
                      <tr key={index} className="border-t align-top">
                        {row.map((cell, position) => (
                          <td key={position} className="py-1 pr-3">
                            {cell}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function GateCard({ gate }: { gate: Gate }) {
  return (
    <li className="space-y-2 rounded-lg border p-4" data-approved={gate.approved ? "yes" : "no"}>
      <div className="flex items-center justify-between">
        <span className="text-lg font-semibold">{gate.gate}</span>
        <Badge tone={gate.approved ? "good" : gate.blockers.length ? "warn" : "info"}>
          {gate.approved ? "approved" : gate.blockers.length ? "blocked" : "ready"}
        </Badge>
      </div>
      <div className="text-xs text-muted-foreground">{gate.role.replaceAll("_", " ")}</div>
      {gate.approved ? (
        <div className="space-y-1 text-xs">
          <div className="flex items-center gap-1 text-emerald-700">
            <CircleCheck className="size-3.5" aria-hidden />
            {gate.decided_at ? new Date(gate.decided_at).toLocaleString("en-SG") : ""}
          </div>
          {gate.snapshot_hash && (
            <div>
              on <code>{gate.snapshot_hash.slice(0, 16)}…</code>
            </div>
          )}
          {gate.comment && <div className="text-muted-foreground">“{gate.comment}”</div>}
        </div>
      ) : (
        <ul className="space-y-1 text-xs text-amber-800">
          {gate.blockers.map((blocker) => (
            <li key={blocker} className="flex items-start gap-1">
              <TriangleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
              {blocker}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

function OutcomePanel({
  bidId,
  outcome,
  onSaved,
  onError,
}: {
  bidId: string;
  outcome: Outcome | null;
  onSaved: () => void;
  onError: (caught: unknown) => void;
}) {
  const [result, setResult] = useState(outcome?.outcome ?? "awarded");
  const [price, setPrice] = useState(outcome?.awarded_price ?? "");
  const [reasons, setReasons] = useState(outcome?.reasons ?? "");
  const [feedback, setFeedback] = useState(outcome?.competitor_feedback ?? "");
  const record = useMutation({
    mutationFn: async () => {
      const { error } = await api.POST("/bids/{bid_id}/outcome", {
        params: { path: { bid_id: bidId } },
        body: {
          outcome: result,
          awarded_price: price ? String(price) : null,
          reasons,
          competitor_feedback: feedback,
        },
      });
      if (error) throw new Error(apiErrorMessage(error, "The outcome was not recorded"));
    },
    onSuccess: onSaved,
    onError,
  });
  return (
    <form
      aria-label="Outcome"
      className="space-y-3 rounded-lg border p-4"
      onSubmit={(event) => {
        event.preventDefault();
        record.mutate();
      }}
    >
      <h2 className="text-lg font-medium">
        Outcome{" "}
        {outcome && (
          <span className="text-sm font-normal text-muted-foreground">
            recorded as {outcome.outcome} by {outcome.recorded_by}
          </span>
        )}
      </h2>
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <select
          aria-label="Result"
          value={result}
          disabled={outcome !== null}
          onChange={(event) => setResult(event.target.value)}
        >
          <option value="awarded">Awarded</option>
          <option value="lost">Lost</option>
          <option value="withdrawn">Withdrawn</option>
        </select>
        <input
          aria-label="Awarded price"
          className="w-40"
          inputMode="decimal"
          placeholder="Awarded price (SGD)"
          value={price}
          onChange={(event) => setPrice(event.target.value)}
        />
      </div>
      <textarea
        aria-label="Reasons"
        className="block min-h-16 w-full"
        placeholder="Why it was won, lost or withdrawn, as far as is known"
        value={reasons}
        onChange={(event) => setReasons(event.target.value)}
      />
      <textarea
        aria-label="Competitor feedback"
        className="block min-h-16 w-full"
        placeholder="What was learned of competitors"
        value={feedback}
        onChange={(event) => setFeedback(event.target.value)}
      />
      <p className="text-xs text-muted-foreground">
        Do not name individuals in either box: what is written here is kept in the audit record,
        which cannot be edited.
      </p>
      <Button type="submit" disabled={record.isPending}>
        {outcome ? "Save the outcome" : "Record the outcome"}
      </Button>
    </form>
  );
}
