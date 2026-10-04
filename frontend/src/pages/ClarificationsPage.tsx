import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, FileQuestion, TriangleAlert } from "lucide-react";
import { useState } from "react";
import { Link, useParams } from "react-router";

import { api, apiErrorMessage } from "@/api/client";
import type { components } from "@/api/schema";
import { accessToken } from "@/auth/oidc";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

/**
 * Tender clarifications (FR-RFI-01, 02, 04, 05, 06, 07).
 *
 * What the platform has flagged that only the client can settle is offered as candidates,
 * related ones grouped; a person drafts one clarification from a candidate or confirms a
 * group. Every draft cites the evidence it rests on, and its options are recommendations.
 * The Bid Manager approves a clarification for issue; one with engineering content needs
 * the Design Manager first. The platform sends nothing: the register is downloaded, sent by
 * a person, and recorded as issued. A response raises a task to assess its impact, and what
 * is unresolved at submission becomes a proposed qualification.
 */

type Register = components["schemas"]["RegisterOut"];
type Clarification = components["schemas"]["ClarificationOut"];
type Candidates = components["schemas"]["CandidatesOut"];
type Candidate = components["schemas"]["CandidateOut"];
type Qualification = components["schemas"]["QualificationOut"];

const TONE: Record<string, "neutral" | "info" | "good" | "warn" | "bad"> = {
  draft: "neutral",
  internal_review: "info",
  approved_to_issue: "info",
  issued: "warn",
  responded: "warn",
  closed_incorporated: "good",
  closed_no_change: "good",
  converted_to_qualification: "neutral",
};

function day(iso: string | null | undefined): string {
  return iso
    ? new Date(iso).toLocaleDateString("en-SG", { day: "2-digit", month: "short", year: "numeric" })
    : "not set";
}

async function download(bidId: string, template: string, format: string) {
  const token = await accessToken();
  const url = new URL(`/api/bids/${bidId}/clarifications/export`, window.location.origin);
  url.searchParams.set("template", template);
  url.searchParams.set("format", format);
  const response = await fetch(url, { headers: token ? { authorization: `Bearer ${token}` } : {} });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(apiErrorMessage(body, "The export did not work"));
  }
  const disposition = response.headers.get("content-disposition") ?? "";
  const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? `clarifications.${format}`;
  const blob = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = blob;
  link.download = name;
  link.click();
  URL.revokeObjectURL(blob);
}

export function ClarificationsPage() {
  const { bidId = "" } = useParams();
  const client = useQueryClient();
  const path = { params: { path: { bid_id: bidId } } };
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [template, setTemplate] = useState("company_default");
  const [format, setFormat] = useState("xlsx");
  const fail = (caught: unknown) =>
    setError(caught instanceof Error ? caught.message : "Something went wrong");
  const refresh = () => {
    setError(null);
    void client.invalidateQueries({ queryKey: ["clarifications", bidId] });
  };

  const register = useQuery({
    queryKey: ["clarifications", bidId, "register"],
    queryFn: async (): Promise<Register> => {
      const { data, error: refused } = await api.GET("/bids/{bid_id}/clarifications", path);
      if (refused || !data) throw new Error("Could not load the clarifications");
      return data;
    },
  });
  const candidates = useQuery({
    queryKey: ["clarifications", bidId, "candidates"],
    queryFn: async (): Promise<Candidates | null> => {
      const { data } = await api.GET("/bids/{bid_id}/clarifications/candidates", path);
      return data ?? null;
    },
  });
  const qualifications = useQuery({
    queryKey: ["clarifications", bidId, "qualifications"],
    queryFn: async (): Promise<Qualification[]> => {
      const { data } = await api.GET("/bids/{bid_id}/qualifications", path);
      return data ?? [];
    },
  });

  const draft = useMutation({
    mutationFn: async (picked: Candidate[]) => {
      const { data, error: refused } = await api.POST("/bids/{bid_id}/clarifications", {
        ...path,
        body: {
          candidates: picked.map((item) => ({ kind: item.kind, ref: item.ref })),
          kind: "tender_clarification",
        },
      });
      if (refused || !data) throw new Error(apiErrorMessage(refused, "Could not draft it"));
      return data;
    },
    onSuccess: (made) => {
      refresh();
      setOpen(made.id);
    },
    onError: fail,
  });
  const prepare = useMutation({
    mutationFn: async () => {
      const { data, error: refused } = await api.POST(
        "/bids/{bid_id}/clarifications/prepare-submission",
        path,
      );
      if (refused || !data) throw new Error(apiErrorMessage(refused, "Could not prepare it"));
      return data;
    },
    onSuccess: refresh,
    onError: fail,
  });
  const decide = useMutation({
    mutationFn: async (input: { id: string; decision: string }) => {
      const { data, error: refused } = await api.POST(
        "/bids/{bid_id}/qualifications/{qualification_id}/decide",
        {
          params: { path: { bid_id: bidId, qualification_id: input.id } },
          body: { decision: input.decision },
        },
      );
      if (refused || !data) throw new Error(apiErrorMessage(refused, "Could not record it"));
      return data;
    },
    onSuccess: refresh,
    onError: fail,
  });

  if (register.isPending) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Loading the clarifications…
      </p>
    );
  }
  if (register.isError) {
    return (
      <p role="alert" className="text-sm text-destructive">
        Could not load the clarifications
      </p>
    );
  }
  const data = register.data;
  const groups = candidates.data?.groups ?? [];
  const selected = data.clarifications.find((row) => row.id === open) ?? null;
  const unresolved = data.clarifications.filter((row) =>
    ["draft", "internal_review", "approved_to_issue", "issued", "responded"].includes(row.state),
  ).length;

  return (
    <section className="space-y-8">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Tender clarifications</h1>
          <p className="text-sm text-muted-foreground">
            Questions for the client before the clarification cut-off
            {data.clarification_cutoff
              ? ` of ${day(data.clarification_cutoff)}; to be issued by ${day(data.due_at)}.`
              : ". The cut-off is not set, so nothing has a due date."}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <select
            aria-label="Template"
            value={template}
            onChange={(event) => setTemplate(event.target.value)}
          >
            {data.templates.map((item) => (
              <option key={item.key} value={item.key}>
                {item.label}
              </option>
            ))}
          </select>
          <select aria-label="Format" value={format} onChange={(event) => setFormat(event.target.value)}>
            <option value="xlsx">Excel</option>
            <option value="docx">Word</option>
          </select>
          <Button
            variant="outline"
            disabled={data.clarifications.length === 0}
            onClick={() => download(bidId, template, format).catch(fail)}
          >
            <Download aria-hidden />
            Download the register
          </Button>
        </div>
      </div>

      {error && (
        <p role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
          {error}
        </p>
      )}

      <div className="space-y-2">
        <h2 className="text-lg font-medium">
          To raise{" "}
          <span className="text-sm font-normal text-muted-foreground">
            flagged issues not yet in a clarification
          </span>
        </h2>
        {groups.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Nothing is flagged that is not already in a clarification.
          </p>
        ) : (
          <ul aria-label="Candidates" className="space-y-3">
            {groups.map((group) => (
              <li key={group.key} className="rounded-lg border p-4">
                {group.candidates.length > 1 && (
                  <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                    <span className="text-sm font-medium">
                      Proposed as one clarification: {group.reason}
                    </span>
                    <Button
                      variant="outline"
                      disabled={draft.isPending}
                      onClick={() => draft.mutate(group.candidates)}
                    >
                      Confirm the group and draft one
                    </Button>
                  </div>
                )}
                <ul className="space-y-3">
                  {group.candidates.map((item) => (
                    <li key={`${item.kind}:${item.ref}`} className="flex items-start gap-3">
                      <FileQuestion className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
                      <div className="min-w-0 flex-1 text-sm">
                        <div className="font-medium">{item.subject}</div>
                        <div className="text-xs text-muted-foreground">
                          {item.kind.replaceAll("_", " ")} · evidence:{" "}
                          {item.evidence.length
                            ? item.evidence.map((ref) => ref.label).join("; ")
                            : "none"}
                        </div>
                      </div>
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={draft.isPending}
                        aria-label={`Draft a clarification for ${item.subject}`}
                        onClick={() => draft.mutate([item])}
                      >
                        Draft
                      </Button>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="space-y-2">
        <h2 className="text-lg font-medium">Register</h2>
        {data.clarifications.length === 0 ? (
          <p className="text-sm text-muted-foreground">No clarification has been drafted.</p>
        ) : (
          <table className="w-full text-sm" aria-label="Clarification register">
            <thead className="text-left">
              <tr>
                <th className="py-2 pr-3">No.</th>
                <th className="py-2 pr-3">Subject</th>
                <th className="py-2 pr-3">Status</th>
                <th className="py-2 pr-3">Due</th>
                <th className="py-2 pr-3">Reviewer</th>
                <th className="py-2" />
              </tr>
            </thead>
            <tbody>
              {data.clarifications.map((row) => (
                <tr key={row.id} className="border-t align-top">
                  <td className="py-2 pr-3 font-medium whitespace-nowrap">{row.number}</td>
                  <td className="py-2 pr-3">
                    {row.subject}
                    {row.engineering_content && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        <Badge tone="warn">Engineering content</Badge>
                        {row.qp_input_needed && <Badge tone="warn">QP input needed</Badge>}
                      </div>
                    )}
                  </td>
                  <td className="py-2 pr-3">
                    <Badge tone={TONE[row.state] ?? "neutral"}>{row.state_label}</Badge>
                  </td>
                  <td
                    className={`py-2 pr-3 whitespace-nowrap ${row.overdue ? "font-medium text-destructive" : ""}`}
                  >
                    {day(row.due_at)}
                    {row.overdue && " · overdue"}
                  </td>
                  <td className="py-2 pr-3">{row.required_reviewer.replaceAll("_", " ")}</td>
                  <td className="py-2 text-right">
                    <Button
                      variant="outline"
                      size="sm"
                      aria-label={`${open === row.id ? "Close" : "Open"} ${row.number}`}
                      onClick={() => setOpen(open === row.id ? null : row.id)}
                    >
                      {open === row.id ? "Close" : "Open"}
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {selected && (
          <ClarificationPanel
            key={selected.id}
            bidId={bidId}
            row={selected}
            onChanged={refresh}
            onError={fail}
          />
        )}
      </div>

      <div className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-lg font-medium">
            Qualifications{" "}
            <span className="text-sm font-normal text-muted-foreground">
              proposed from clarifications unresolved at submission
            </span>
          </h2>
          <Button
            variant="outline"
            disabled={prepare.isPending || unresolved === 0}
            onClick={() => prepare.mutate()}
          >
            Prepare the submission ({unresolved} unresolved)
          </Button>
        </div>
        {(qualifications.data ?? []).length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing is proposed.</p>
        ) : (
          <ul aria-label="Qualifications" className="divide-y rounded-lg border">
            {(qualifications.data ?? []).map((item) => (
              <li key={item.id} className="flex items-start gap-3 p-3 text-sm">
                <div className="min-w-0 flex-1">
                  <div className="mb-1 flex flex-wrap items-center gap-2">
                    <Badge tone={item.kind === "qualification" ? "warn" : "info"}>{item.kind}</Badge>
                    {item.clarification_number && (
                      <span className="text-xs text-muted-foreground">
                        from {item.clarification_number}
                      </span>
                    )}
                    <span className="text-xs text-muted-foreground">
                      {item.state}
                      {item.decided_by ? ` by ${item.decided_by}` : ""}
                    </span>
                  </div>
                  {item.text}
                </div>
                {item.state === "proposed" && (
                  <div className="flex shrink-0 gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => decide.mutate({ id: item.id, decision: "accepted" })}
                    >
                      Accept
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => decide.mutate({ id: item.id, decision: "rejected" })}
                    >
                      Reject
                    </Button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <dt className="text-[11px] font-semibold tracking-wider text-muted-foreground uppercase">
        {label}
      </dt>
      <dd className="mt-0.5 text-sm">{children}</dd>
    </div>
  );
}

function ClarificationPanel({
  bidId,
  row,
  onChanged,
  onError,
}: {
  bidId: string;
  row: Clarification;
  onChanged: () => void;
  onError: (caught: unknown) => void;
}) {
  const path = { params: { path: { bid_id: bidId, clarification_id: row.id } } };
  const editable = row.state === "draft" || row.state === "internal_review";
  const [problem, setProblem] = useState(row.problem);
  const [cost, setCost] = useState(row.cost_impact);
  const [programme, setProgramme] = useState(row.programme_impact);
  const [qp, setQp] = useState(false);
  const [summary, setSummary] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [outcome, setOutcome] = useState("incorporated");
  const [note, setNote] = useState("");
  const [rerun, setRerun] = useState(true);

  const done = { onSuccess: onChanged, onError };
  const move = useMutation({
    mutationFn: async (target: string) => {
      const { error } = await api.POST(
        "/bids/{bid_id}/clarifications/{clarification_id}/transition",
        { ...path, body: { target } },
      );
      if (error) throw new Error(apiErrorMessage(error, "Could not move it"));
    },
    ...done,
  });
  const save = useMutation({
    mutationFn: async () => {
      const { error } = await api.PATCH("/bids/{bid_id}/clarifications/{clarification_id}", {
        ...path,
        body: { problem, cost_impact: cost, programme_impact: programme },
      });
      if (error) throw new Error(apiErrorMessage(error, "Could not save it"));
    },
    ...done,
  });
  const approve = useMutation({
    mutationFn: async () => {
      const { error } = await api.POST(
        "/bids/{bid_id}/clarifications/{clarification_id}/design-approval",
        { ...path, body: { qp_input_needed: qp } },
      );
      if (error) throw new Error(apiErrorMessage(error, "Could not record the approval"));
    },
    ...done,
  });
  const reword = useMutation({
    mutationFn: async () => {
      const { error } = await api.POST(
        "/bids/{bid_id}/clarifications/{clarification_id}/draft-with-model",
        path,
      );
      if (error) throw new Error(apiErrorMessage(error, "The model's wording was not accepted"));
    },
    ...done,
  });
  const respond = useMutation({
    mutationFn: async () => {
      const body = new FormData();
      body.append("summary", summary);
      if (file) body.append("file", file);
      const token = await accessToken();
      const response = await fetch(
        new URL(`/api/bids/${bidId}/clarifications/${row.id}/response`, window.location.origin),
        { method: "POST", headers: token ? { authorization: `Bearer ${token}` } : {}, body },
      );
      if (!response.ok) {
        const json = await response.json().catch(() => null);
        throw new Error(apiErrorMessage(json, "The response was not recorded"));
      }
    },
    ...done,
  });
  const assess = useMutation({
    mutationFn: async () => {
      const { error } = await api.POST(
        "/bids/{bid_id}/clarifications/{clarification_id}/impact",
        { ...path, body: { outcome, note, rerun } },
      );
      if (error) throw new Error(apiErrorMessage(error, "Could not record the assessment"));
    },
    ...done,
  });
  const busy =
    move.isPending || save.isPending || approve.isPending || respond.isPending || assess.isPending;
  const waitingOnDesign = row.engineering_content && !row.design_approved_by;

  return (
    <section
      aria-label={`Clarification ${row.number}`}
      className="space-y-4 rounded-lg border bg-muted/30 p-5"
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <h3 className="text-base font-semibold">
          {row.number} · {row.subject}
        </h3>
        <Badge tone={TONE[row.state] ?? "neutral"}>{row.state_label}</Badge>
      </div>

      {row.engineering_content && (
        <p className="flex items-start gap-2 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
          <span>
            Engineering content ({row.engineering_reason}):{" "}
            {row.design_approved_by
              ? `approved by the Design Manager, ${row.design_approved_by}`
              : "the Design Manager must approve it before issue"}
            {row.qp_input_needed ? "; QP input needed" : ""}.
          </span>
        </p>
      )}

      <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <Field label="Project">{row.project}</Field>
        <Field label="Level / grid">{row.level_grid || "not stated"}</Field>
        <Field label="Sheet and revision">
          {row.sheets.length
            ? row.sheets.map((sheet) => (
                <div key={sheet.sheet_number}>
                  {sheet.sheet_id ? (
                    <Link to={`/bids/${bidId}/sheets/${sheet.sheet_id}`}>{sheet.sheet_number}</Link>
                  ) : (
                    sheet.sheet_number
                  )}
                  {sheet.revision ? ` rev ${sheet.revision}` : ""}
                </div>
              ))
            : "none"}
        </Field>
        <Field label="Required reviewer">{row.required_reviewer.replaceAll("_", " ")}</Field>
      </dl>

      <div className="space-y-1">
        <label htmlFor="clarification-problem" className="text-sm font-medium">
          Query
        </label>
        {editable ? (
          <textarea
            id="clarification-problem"
            className="block min-h-28 w-full"
            value={problem}
            onChange={(event) => setProblem(event.target.value)}
          />
        ) : (
          <p className="text-sm whitespace-pre-line">{row.problem}</p>
        )}
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-1">
          <h4 className="text-sm font-medium">Evidence</h4>
          <ul aria-label="Evidence" className="space-y-2 text-sm">
            {row.evidence.map((item) => (
              <li key={`${item.label}:${item.quote}`} className="rounded-md border bg-card p-2">
                <div className="font-medium">
                  {item.label}
                  {item.revision ? ` rev ${item.revision}` : ""}
                </div>
                {item.quote && <div className="text-xs text-muted-foreground">“{item.quote}”</div>}
              </li>
            ))}
          </ul>
        </div>
        <div className="space-y-1">
          <h4 className="text-sm font-medium">
            Options{" "}
            <span className="text-xs font-normal text-muted-foreground">
              recommendations only: the client decides
            </span>
          </h4>
          {row.options.length === 0 ? (
            <p className="text-sm text-muted-foreground">None offered.</p>
          ) : (
            <ul aria-label="Options" className="list-disc space-y-1 pl-5 text-sm">
              {row.options.map((option) => (
                <li key={option.text}>{option.text}</li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-1">
          <label htmlFor="clarification-cost" className="text-sm font-medium">
            Potential cost impact
          </label>
          {editable ? (
            <input
              id="clarification-cost"
              className="block w-full"
              value={cost}
              placeholder="Not assessed"
              onChange={(event) => setCost(event.target.value)}
            />
          ) : (
            <p className="text-sm">{row.cost_impact || "Not assessed"}</p>
          )}
        </div>
        <div className="space-y-1">
          <label htmlFor="clarification-programme" className="text-sm font-medium">
            Potential programme impact
          </label>
          {editable ? (
            <input
              id="clarification-programme"
              className="block w-full"
              value={programme}
              placeholder="Not assessed"
              onChange={(event) => setProgramme(event.target.value)}
            />
          ) : (
            <p className="text-sm">{row.programme_impact || "Not assessed"}</p>
          )}
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 border-t pt-4">
        {editable && (
          <Button variant="outline" disabled={busy} onClick={() => save.mutate()}>
            Save changes
          </Button>
        )}
        {row.state === "draft" && (
          <>
            <Button variant="outline" disabled={busy || reword.isPending} onClick={() => reword.mutate()}>
              Improve the wording with the model
            </Button>
            <Button disabled={busy} onClick={() => move.mutate("internal_review")}>
              Send for internal review
            </Button>
          </>
        )}
        {row.state === "internal_review" && (
          <>
            <Button variant="outline" disabled={busy} onClick={() => move.mutate("draft")}>
              Send back to draft
            </Button>
            {waitingOnDesign && (
              <>
                <label className="flex items-center gap-1.5 text-sm">
                  <input type="checkbox" checked={qp} onChange={(event) => setQp(event.target.checked)} />
                  QP input needed
                </label>
                <Button variant="outline" disabled={busy} onClick={() => approve.mutate()}>
                  Approve as Design Manager
                </Button>
              </>
            )}
            <Button disabled={busy} onClick={() => move.mutate("approved_to_issue")}>
              Approve to issue
            </Button>
          </>
        )}
        {row.state === "approved_to_issue" && (
          <>
            <span className="text-sm text-muted-foreground">
              Download the register and send it; the platform sends nothing.
            </span>
            <Button disabled={busy} onClick={() => move.mutate("issued")}>
              Record as issued
            </Button>
          </>
        )}
      </div>

      {row.state === "issued" && (
        <form
          className="space-y-2 border-t pt-4"
          onSubmit={(event) => {
            event.preventDefault();
            respond.mutate();
          }}
        >
          <label htmlFor="clarification-response" className="text-sm font-medium">
            The client's response
          </label>
          <textarea
            id="clarification-response"
            className="block min-h-20 w-full"
            placeholder="What the response says"
            value={summary}
            onChange={(event) => setSummary(event.target.value)}
          />
          <div className="flex flex-wrap items-center gap-3">
            <input
              type="file"
              aria-label="Response file"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
            <Button type="submit" disabled={busy || !summary.trim()}>
              Record the response
            </Button>
          </div>
        </form>
      )}

      {row.response_summary && (
        <div className="space-y-1 border-t pt-4 text-sm">
          <h4 className="font-medium">Response, {day(row.responded_at)}</h4>
          <p className="whitespace-pre-line">{row.response_summary}</p>
        </div>
      )}

      {row.state === "responded" && (
        <form
          className="space-y-2 border-t pt-4"
          onSubmit={(event) => {
            event.preventDefault();
            assess.mutate();
          }}
        >
          <h4 className="text-sm font-medium">Assess the impact</h4>
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <select aria-label="Outcome" value={outcome} onChange={(event) => setOutcome(event.target.value)}>
              <option value="incorporated">Incorporated</option>
              <option value="no_change">No change</option>
            </select>
            <label className="flex items-center gap-1.5">
              <input
                type="checkbox"
                checked={rerun}
                onChange={(event) => setRerun(event.target.checked)}
              />
              Run takeoff and pricing again first
            </label>
          </div>
          <textarea
            aria-label="What the response changes"
            className="block min-h-16 w-full"
            placeholder="What the response changes, or why it changes nothing"
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
          <Button type="submit" disabled={busy || !note.trim()}>
            Record the assessment and close
          </Button>
        </form>
      )}

      {row.impact_outcome && (
        <p className="border-t pt-4 text-sm">
          <span className="font-medium">Impact, by {row.impact_by}:</span> {row.impact_note}
        </p>
      )}
    </section>
  );
}
