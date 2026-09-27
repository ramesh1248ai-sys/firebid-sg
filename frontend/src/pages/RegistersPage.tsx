import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useMemo, useState } from "react";
import { Link, useParams } from "react-router";

import { api, apiErrorMessage } from "@/api/client";
import type { components } from "@/api/schema";
import { accessToken } from "@/auth/oidc";
import { Button } from "@/components/ui/button";

/**
 * The drawing and specification registers (FR-DOC-03), and the decisions on them.
 *
 * The page exists so an estimator can say "this is the set" with confidence. So the first
 * thing on it is what still stands in the way of saying that: conflicts, sheets nobody has
 * identified, document types nobody has confirmed. Each of those rows carries the action that
 * clears it, and "Register confirmed" stays unavailable until there are none.
 */

type DrawingRow = components["schemas"]["DrawingRowOut"];
type DocumentRow = components["schemas"]["DocumentRowOut"];
type Status = components["schemas"]["RegisterStatus"];

const STATE_LABELS: Record<string, { label: string; tone: string }> = {
  current: { label: "Current", tone: "text-emerald-700 dark:text-emerald-400" },
  superseded: { label: "Superseded", tone: "text-muted-foreground" },
  conflict: { label: "Conflict", tone: "text-red-700 dark:text-red-400" },
  received: {
    label: "To identify",
    tone: "text-amber-700 dark:text-amber-400",
  },
  registered: { label: "Registered", tone: "" },
  withdrawn: { label: "Withdrawn", tone: "text-muted-foreground line-through" },
};

const DOC_TYPES = [
  "drawing",
  "specification",
  "boq",
  "schedule",
  "addendum",
  "clarification_response",
  "contract_conditions",
  "other",
] as const;

function typeLabel(value: string | null | undefined): string {
  return value ? value.replaceAll("_", " ") : "unclassified";
}

function StateBadge({ state }: { state: string }) {
  const shown = STATE_LABELS[state] ?? { label: state, tone: "" };
  return (
    <span className={`text-xs font-medium ${shown.tone}`}>{shown.label}</span>
  );
}

export function RegistersPage() {
  const { bidId = "" } = useParams();
  const [tab, setTab] = useState<"drawings" | "documents">("drawings");
  const status = useQuery({
    queryKey: ["registers", bidId, "status"],
    queryFn: async (): Promise<Status> => {
      const { data, error } = await api.GET("/bids/{bid_id}/registers/status", {
        params: { path: { bid_id: bidId } },
      });
      if (error || !data) throw new Error("Could not read the register status");
      return data;
    },
  });

  return (
    <section className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Registers</h1>
        <p className="text-sm text-muted-foreground">
          Every drawing and document in the tender set, with the one revision of
          each that is current.
        </p>
      </div>

      {status.data && <StatusBar bidId={bidId} status={status.data} />}

      <div role="tablist" className="flex gap-2 border-b">
        {(
          [
            ["drawings", "Drawing register"],
            ["documents", "Specification register"],
          ] as const
        ).map(([key, label]) => (
          <button
            key={key}
            role="tab"
            aria-selected={tab === key}
            onClick={() => setTab(key)}
            className={`-mb-px border-b-2 px-3 py-2 text-sm ${
              tab === key
                ? "border-primary font-medium"
                : "border-transparent text-muted-foreground"
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {tab === "drawings" ? (
        <DrawingRegister bidId={bidId} />
      ) : (
        <DocumentRegister bidId={bidId} />
      )}

      <Link to={`/bids/${bidId}`} className="inline-block text-sm underline">
        Back to the bid
      </Link>
    </section>
  );
}

function StatusBar({ bidId, status }: { bidId: string; status: Status }) {
  const queryClient = useQueryClient();
  const confirm = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST(
        "/bids/{bid_id}/registers/confirm",
        {
          params: { path: { bid_id: bidId } },
          body: {},
        },
      );
      if (error || !data)
        throw new Error(apiErrorMessage(error, "Could not confirm"));
      return data;
    },
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["registers", bidId] }),
  });

  const blockers = [
    status.conflicts ? `${status.conflicts} in conflict` : null,
    status.unidentified ? `${status.unidentified} to identify` : null,
    status.unsure_types
      ? `${status.unsure_types} document types to confirm`
      : null,
  ].filter(Boolean);

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border p-4">
      <div className="text-sm" role="status">
        {status.confirmation ? (
          <p>
            Register confirmed on{" "}
            {new Date(status.confirmation.confirmed_at).toLocaleDateString(
              "en-SG",
            )}
            : {status.confirmation.drawings} drawings,{" "}
            {status.confirmation.documents} documents.
          </p>
        ) : blockers.length ? (
          <p>Before the register can be confirmed: {blockers.join(", ")}.</p>
        ) : (
          <p>Every drawing and document has one current revision.</p>
        )}
        {confirm.error && (
          <p role="alert" className="text-destructive">
            {confirm.error.message}
          </p>
        )}
      </div>
      <Button
        disabled={!status.ready || confirm.isPending}
        onClick={() => confirm.mutate()}
      >
        Register confirmed
      </Button>
    </div>
  );
}

async function download(bidId: string, kind: "drawings" | "documents") {
  const token = await accessToken();
  const response = await fetch(
    new URL(
      `/api/bids/${bidId}/registers/${kind}.xlsx`,
      window.location.origin,
    ),
    { headers: token ? { authorization: `Bearer ${token}` } : {} },
  );
  if (!response.ok) throw new Error("The register could not be exported");
  const disposition = response.headers.get("content-disposition") ?? "";
  const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? `${kind}.xlsx`;
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

function ExportButton({
  bidId,
  kind,
}: {
  bidId: string;
  kind: "drawings" | "documents";
}) {
  const [failed, setFailed] = useState(false);
  return (
    <span className="inline-flex items-center gap-2">
      <Button
        variant="outline"
        onClick={() => {
          setFailed(false);
          download(bidId, kind).catch(() => setFailed(true));
        }}
      >
        Export to Excel
      </Button>
      {failed && (
        <span className="text-sm text-destructive">Export failed</span>
      )}
    </span>
  );
}

function useDecision(bidId: string, path: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: Record<string, unknown>) => {
      const token = await accessToken();
      const response = await fetch(
        new URL(`/api/bids/${bidId}/${path}`, window.location.origin),
        {
          method: "POST",
          headers: {
            "content-type": "application/json",
            ...(token ? { authorization: `Bearer ${token}` } : {}),
          },
          body: JSON.stringify(body),
        },
      );
      if (!response.ok) {
        throw new Error(
          apiErrorMessage(
            await response.json().catch(() => null),
            "That did not save",
          ),
        );
      }
      return response.json();
    },
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["registers", bidId] }),
  });
}

// --- Drawings -------------------------------------------------------------------------------

function DrawingRegister({ bidId }: { bidId: string }) {
  const [filters, setFilters] = useState({
    discipline: "",
    level: "",
    state: "",
  });
  const rows = useQuery({
    queryKey: ["registers", bidId, "drawings"],
    queryFn: async (): Promise<DrawingRow[]> => {
      const { data, error } = await api.GET(
        "/bids/{bid_id}/registers/drawings",
        {
          params: { path: { bid_id: bidId } },
        },
      );
      if (error || !data)
        throw new Error("Could not load the drawing register");
      return data;
    },
  });

  const all = useMemo(() => rows.data ?? [], [rows.data]);
  const options = useMemo(
    () => ({
      discipline: [
        ...new Set(all.map((row) => row.discipline).filter(Boolean)),
      ] as string[],
      level: [
        ...new Set(all.map((row) => row.level).filter(Boolean)),
      ] as string[],
      state: [...new Set(all.map((row) => row.state))],
    }),
    [all],
  );
  const shown = all.filter(
    (row) =>
      (!filters.discipline || row.discipline === filters.discipline) &&
      (!filters.level || row.level === filters.level) &&
      (!filters.state || row.state === filters.state),
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-3">
        {(["discipline", "level", "state"] as const).map((name) => (
          <label key={name} className="text-sm">
            <span className="block capitalize text-muted-foreground">
              {name}
            </span>
            <select
              aria-label={`Filter by ${name}`}
              className="rounded-md border bg-background px-2 py-1"
              value={filters[name]}
              onChange={(event) =>
                setFilters({ ...filters, [name]: event.target.value })
              }
            >
              <option value="">All</option>
              {options[name].map((value) => (
                <option key={value} value={value}>
                  {name === "state"
                    ? (STATE_LABELS[value]?.label ?? value)
                    : value}
                </option>
              ))}
            </select>
          </label>
        ))}
        <ExportButton bidId={bidId} kind="drawings" />
      </div>

      {rows.isLoading && (
        <p className="text-sm text-muted-foreground">Loading…</p>
      )}
      {rows.data && shown.length === 0 && (
        <p className="text-sm text-muted-foreground">No drawings match.</p>
      )}
      {shown.length > 0 && (
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="py-2">Drawing</th>
              <th>Title</th>
              <th>Rev</th>
              <th>Date</th>
              <th>Status</th>
              <th>Level</th>
              <th>Accuracy</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((row) => (
              <DrawingLine key={row.revision_id} bidId={bidId} row={row} />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function DrawingLine({ bidId, row }: { bidId: string; row: DrawingRow }) {
  const date = row.revision_date
    ? new Date(row.revision_date).toLocaleDateString("en-SG")
    : "";
  return (
    <>
      <tr className="border-t align-top">
        <td className="py-2 font-medium">
          <Link
            to={`/bids/${bidId}/sheets/${row.sheet_id}`}
            className="hover:underline"
          >
            {row.sheet_number ?? row.filename}
          </Link>
        </td>
        <td>{row.title}</td>
        <td>{row.revision}</td>
        <td>{date}</td>
        <td>
          <StateBadge state={row.state} />
        </td>
        <td>{row.level}</td>
        <td>
          {row.quality_band ?? ""}
          {row.manual_takeoff_recommended && (
            <span className="block text-xs text-amber-700 dark:text-amber-400">
              Manual takeoff recommended
            </span>
          )}
        </td>
      </tr>
      {row.state === "conflict" && (
        <tr>
          <td colSpan={7} className="pb-3">
            <p className="mb-2 text-sm text-red-700 dark:text-red-400">
              {row.conflict_reason}
            </p>
            <ResolveForm
              bidId={bidId}
              path={`sheet-revisions/${row.revision_id}/resolve`}
            />
          </td>
        </tr>
      )}
      {row.state === "received" && (
        <tr>
          <td colSpan={7} className="pb-3">
            <IdentifySheetForm bidId={bidId} row={row} />
          </td>
        </tr>
      )}
    </>
  );
}

function IdentifySheetForm({ bidId, row }: { bidId: string; row: DrawingRow }) {
  const confirm = useDecision(
    bidId,
    `sheet-revisions/${row.revision_id}/confirm`,
  );
  const withdraw = useDecision(
    bidId,
    `sheet-revisions/${row.revision_id}/withdraw`,
  );
  const [number, setNumber] = useState(row.sheet_number ?? "");
  const [revision, setRevision] = useState(row.revision ?? "");
  const [consultant, setConsultant] = useState("");

  function submit(event: FormEvent) {
    event.preventDefault();
    confirm.mutate({
      sheet_number: number,
      revision,
      consultant: consultant || undefined,
    });
  }

  return (
    <form onSubmit={submit} className="flex flex-wrap items-end gap-2 text-sm">
      <span className="w-full text-muted-foreground">
        {row.filename}: the title block was not read with confidence. Say what
        this sheet is.
      </span>
      <label>
        <span className="block text-xs text-muted-foreground">
          Drawing number
        </span>
        <input
          className="rounded-md border bg-background px-2 py-1"
          value={number}
          onChange={(event) => setNumber(event.target.value)}
          required
        />
      </label>
      <label>
        <span className="block text-xs text-muted-foreground">Revision</span>
        <input
          className="w-20 rounded-md border bg-background px-2 py-1"
          value={revision}
          onChange={(event) => setRevision(event.target.value)}
          required
        />
      </label>
      <label>
        <span className="block text-xs text-muted-foreground">
          Consultant (remember this layout)
        </span>
        <input
          className="rounded-md border bg-background px-2 py-1"
          value={consultant}
          onChange={(event) => setConsultant(event.target.value)}
        />
      </label>
      <Button type="submit" disabled={confirm.isPending}>
        Confirm
      </Button>
      <Button
        type="button"
        variant="outline"
        disabled={withdraw.isPending}
        onClick={() => withdraw.mutate({ reason: "Not a drawing" })}
      >
        Not a drawing
      </Button>
      {(confirm.error ?? withdraw.error) && (
        <span role="alert" className="text-destructive">
          {(confirm.error ?? withdraw.error)?.message}
        </span>
      )}
    </form>
  );
}

function ResolveForm({ bidId, path }: { bidId: string; path: string }) {
  const resolve = useDecision(bidId, path);
  const [outcome, setOutcome] = useState("current");
  const [revision, setRevision] = useState("");
  const [reason, setReason] = useState("");

  function submit(event: FormEvent) {
    event.preventDefault();
    resolve.mutate({ outcome, reason, revision: revision || undefined });
  }

  return (
    <form onSubmit={submit} className="flex flex-wrap items-end gap-2 text-sm">
      <label>
        <span className="block text-xs text-muted-foreground">Decision</span>
        <select
          aria-label="Decision"
          className="rounded-md border bg-background px-2 py-1"
          value={outcome}
          onChange={(event) => setOutcome(event.target.value)}
        >
          <option value="current">This revision is current</option>
          <option value="superseded">This revision is superseded</option>
          <option value="withdrawn">Withdraw it</option>
        </select>
      </label>
      <label>
        <span className="block text-xs text-muted-foreground">
          Correct revision (optional)
        </span>
        <input
          className="w-20 rounded-md border bg-background px-2 py-1"
          value={revision}
          onChange={(event) => setRevision(event.target.value)}
        />
      </label>
      <label className="grow">
        <span className="block text-xs text-muted-foreground">Reason</span>
        <input
          aria-label="Reason"
          className="w-full rounded-md border bg-background px-2 py-1"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          required
          minLength={3}
        />
      </label>
      <Button type="submit" disabled={resolve.isPending}>
        Resolve
      </Button>
      {resolve.error && (
        <span role="alert" className="text-destructive">
          {resolve.error.message}
        </span>
      )}
    </form>
  );
}

// --- Documents ------------------------------------------------------------------------------

function DocumentRegister({ bidId }: { bidId: string }) {
  const rows = useQuery({
    queryKey: ["registers", bidId, "documents"],
    queryFn: async (): Promise<DocumentRow[]> => {
      const { data, error } = await api.GET(
        "/bids/{bid_id}/registers/documents",
        {
          params: { path: { bid_id: bidId } },
        },
      );
      if (error || !data)
        throw new Error("Could not load the specification register");
      return data;
    },
  });

  return (
    <div className="space-y-4">
      <ExportButton bidId={bidId} kind="documents" />
      {rows.data && rows.data.length === 0 && (
        <p className="text-sm text-muted-foreground">No documents yet.</p>
      )}
      {rows.data && rows.data.length > 0 && (
        <table className="w-full text-sm">
          <thead className="text-left text-xs uppercase tracking-wide text-muted-foreground">
            <tr>
              <th className="py-2">Document</th>
              <th>Type</th>
              <th>Rev</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.data.map((row) => (
              <DocumentLine key={row.revision_id} bidId={bidId} row={row} />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function DocumentLine({ bidId, row }: { bidId: string; row: DocumentRow }) {
  const setType = useDecision(bidId, `documents/${row.document_id}/type`);
  const unsure =
    row.type_decided_by !== "person" && (row.type_confidence ?? 0) < 0.8;
  return (
    <>
      <tr className="border-t align-top">
        <td className="py-2">
          <span className="font-medium">
            {row.title ?? row.doc_key ?? row.filename}
          </span>
          <span className="block text-xs text-muted-foreground">
            {row.filename}
          </span>
        </td>
        <td>
          {unsure ? (
            <select
              aria-label={`Type of ${row.filename}`}
              className="rounded-md border bg-background px-2 py-1"
              defaultValue={row.doc_type ?? ""}
              onChange={(event) =>
                setType.mutate({ doc_type: event.target.value })
              }
            >
              {DOC_TYPES.map((value) => (
                <option key={value} value={value}>
                  {typeLabel(value)}
                </option>
              ))}
            </select>
          ) : (
            typeLabel(row.doc_type)
          )}
        </td>
        <td>{row.revision}</td>
        <td>
          <StateBadge state={row.state} />
        </td>
      </tr>
      {row.state === "conflict" && (
        <tr>
          <td colSpan={4} className="pb-3">
            <p className="mb-2 text-sm text-red-700 dark:text-red-400">
              {row.conflict_reason}
            </p>
            <ResolveForm
              bidId={bidId}
              path={`document-revisions/${row.revision_id}/resolve`}
            />
          </td>
        </tr>
      )}
    </>
  );
}
