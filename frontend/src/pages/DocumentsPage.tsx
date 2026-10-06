import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";

import { api, apiErrorMessage } from "@/api/client";
import { AuthorisedImage } from "@/components/AuthorisedImage";
import { Button } from "@/components/ui/button";
import { accessToken } from "@/auth/oidc";
import { FolderUpload } from "@/pages/FolderUpload";

/**
 * The tender set: send files, watch them land, and see what was refused.
 *
 * The refusals are the reason this page exists. An estimator prices what they can see, so a
 * set that quietly lost four drawings is worse than one that says so loudly — every file is
 * accounted for here, with a reason next to the ones that did not make it.
 */

type Progress = {
  total: number;
  counts: Record<string, number>;
  sheets: number;
  sheets_parsed?: number;
  finished: boolean;
  failures: {
    id: string;
    filename: string;
    state: string;
    reason: string | null;
  }[];
  // A drawing can be "done" with a sheet in it unread.
  unread_sheets?: {
    id: string;
    filename: string;
    page: number;
    reason: string;
  }[];
  // What asking for the set to be read again would reach.
  read_again?: { documents: number; sheets: number; views: number };
};

type Sheet = {
  id: string;
  document_id: string;
  filename: string;
  layout_name: string | null;
  width_mm: number | null;
  height_mm: number | null;
  content_class: string | null;
  has_thumbnail: boolean;
};

/** How each state reads to a person, and whether it needs attention. */
const STATE_LABELS: Record<
  string,
  { label: string; tone: "good" | "working" | "bad" }
> = {
  done: { label: "Ready", tone: "good" },
  processing: { label: "Being read", tone: "working" },
  received: { label: "Queued", tone: "working" },
  awaiting_scan: { label: "Waiting for the scanner", tone: "bad" },
  rejected: { label: "Refused", tone: "bad" },
  quarantined: { label: "Quarantined", tone: "bad" },
};

const TONE_CLASSES = {
  good: "text-emerald-700 dark:text-emerald-400",
  working: "text-muted-foreground",
  bad: "text-amber-700 dark:text-amber-400",
} as const;

/**
 * Follow the bid's progress over server-sent events, falling back to the one-shot endpoint.
 *
 * EventSource cannot carry an Authorization header, so this uses fetch with a reader. That
 * also means a dropped stream does not reconnect by itself, which is why the query below
 * keeps its own polling as a safety net.
 */
function useProgress(bidId: string) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["progress", bidId],
    queryFn: async (): Promise<Progress> => {
      const { data, error } = await api.GET("/bids/{bid_id}/progress", {
        params: { path: { bid_id: bidId } },
      });
      if (error || !data) throw new Error("Could not read the upload progress");
      return data as Progress;
    },
    // Only while something is still moving: a settled bid does not need polling.
    refetchInterval: (query) =>
      query.state.data?.finished === false ? 4000 : false,
  });

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;

    async function follow() {
      const token = await accessToken();
      const response = await fetch(
        new URL(`/api/bids/${bidId}/progress/stream`, window.location.origin),
        {
          headers: token ? { authorization: `Bearer ${token}` } : {},
          signal: controller.signal,
        },
      );
      if (!response.body) return;
      const reader = response.body
        .pipeThrough(new TextDecoderStream())
        .getReader();
      let buffer = "";
      while (!cancelled) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += value;
        // SSE frames are separated by a blank line; a partial frame stays in the buffer.
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";
        for (const frame of frames) {
          const line = frame
            .split("\n")
            .find((candidate) => candidate.startsWith("data: "));
          if (!line) continue;
          queryClient.setQueryData(
            ["progress", bidId],
            JSON.parse(line.slice(6)) as Progress,
          );
          queryClient.invalidateQueries({ queryKey: ["sheets", bidId] });
        }
      }
    }

    follow().catch(() => {
      // The stream is a nicety; the polling query above is what guarantees the page updates.
    });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [bidId, queryClient]);

  return query;
}

function useSheets(bidId: string, readyCount: number | undefined) {
  const queryClient = useQueryClient();
  // Refetch whenever the count of ready sheets moves, whether the stream or the polling
  // noticed; otherwise a page without the stream would count sheets it never lists.
  useEffect(() => {
    if (readyCount !== undefined)
      queryClient.invalidateQueries({ queryKey: ["sheets", bidId] });
  }, [bidId, readyCount, queryClient]);

  return useQuery({
    queryKey: ["sheets", bidId],
    queryFn: async (): Promise<Sheet[]> => {
      const { data, error } = await api.GET("/bids/{bid_id}/sheets", {
        params: { path: { bid_id: bidId } },
      });
      if (error || !data) throw new Error("Could not load the sheets");
      return data as Sheet[];
    },
  });
}

function useUpload(bidId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      files,
      addendumId,
    }: {
      files: FileList;
      addendumId: string;
    }) => {
      const body = new FormData();
      for (const file of files) body.append("files", file);
      // An addendum's files are linked to it, and its date orders what they replace.
      if (addendumId) body.append("addendum_id", addendumId);
      const token = await accessToken();
      const response = await fetch(
        new URL(`/api/bids/${bidId}/documents`, window.location.origin),
        {
          method: "POST",
          headers: token ? { authorization: `Bearer ${token}` } : {},
          body,
        },
      );
      if (!response.ok) {
        throw new Error(
          apiErrorMessage(
            await response.json().catch(() => null),
            "Upload failed",
          ),
        );
      }
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["progress", bidId] });
      queryClient.invalidateQueries({ queryKey: ["sheets", bidId] });
    },
  });
}

function useReadAgain(bidId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST(
        "/bids/{bid_id}/documents/read-again",
        { params: { path: { bid_id: bidId } } },
      );
      if (error || !data)
        throw new Error(apiErrorMessage(error, "Could not read the set again"));
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["progress", bidId] });
      queryClient.invalidateQueries({ queryKey: ["sheets", bidId] });
    },
  });
}

/** "2 drawings", "1 drawing": a count with its noun. */
function counted(count: number, noun: string) {
  return `${count} ${noun}${count === 1 ? "" : "s"}`;
}

/**
 * What was not read, and the one action that reads it again.
 *
 * A sheet that failed sits inside a drawing that says "Ready", so it is listed here or
 * nobody would know. The button is offered only when there is something for it to do.
 */
function ReadAgain({ bidId, progress }: { bidId: string; progress: Progress }) {
  const readAgain = useReadAgain(bidId);
  const unread = progress.unread_sheets ?? [];
  const reach = progress.read_again ?? { documents: 0, sheets: 0, views: 0 };
  if (unread.length === 0 && reach.documents === 0 && reach.views === 0)
    return null;

  const parts = [
    reach.documents > 0 && counted(reach.documents, "drawing"),
    reach.views > 0 &&
      `the views of ${counted(reach.views, "sheet")} found by an older version`,
  ].filter(Boolean);

  return (
    <div className="space-y-3 rounded-lg border border-amber-300 p-4 dark:border-amber-700">
      {unread.length > 0 && (
        <>
          <h2 className="text-sm font-semibold">
            {unread.length === 1
              ? "1 sheet could not be read"
              : `${unread.length} sheets could not be read`}
          </h2>
          <ul className="space-y-2">
            {unread.map((sheet) => (
              <li key={sheet.id} className="text-sm">
                <span className="font-medium">{sheet.filename}</span>{" "}
                <span className="text-muted-foreground">
                  page {sheet.page}
                </span>
                <p className="text-xs text-muted-foreground">{sheet.reason}</p>
              </li>
            ))}
          </ul>
        </>
      )}
      {parts.length > 0 && (
        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="button"
            variant="outline"
            disabled={readAgain.isPending}
            onClick={() => readAgain.mutate()}
          >
            {readAgain.isPending ? "Queuing…" : "Read again"}
          </Button>
          <p className="text-sm text-muted-foreground">
            Reads again {parts.join(" and ")}. Nothing that was read is lost.
          </p>
        </div>
      )}
      {readAgain.isError && (
        <p role="alert" className="text-sm text-destructive">
          {readAgain.error.message}
        </p>
      )}
    </div>
  );
}

function Counts({ progress }: { progress: Progress }) {
  const shown = Object.entries(progress.counts).filter(
    ([, count]) => count > 0,
  );
  if (shown.length === 0) return null;
  return (
    <dl className="flex flex-wrap gap-x-6 gap-y-2">
      {shown.map(([state, count]) => {
        const meta = STATE_LABELS[state] ?? {
          label: state,
          tone: "working" as const,
        };
        return (
          <div key={state}>
            <dt className="text-xs uppercase tracking-wide text-muted-foreground">
              {meta.label}
            </dt>
            <dd className={`text-lg font-semibold ${TONE_CLASSES[meta.tone]}`}>
              {count}
            </dd>
          </div>
        );
      })}
    </dl>
  );
}

/** A sheet's thumbnail, fetched with the sign-in token. */
function Thumbnail({ path }: { path: string }) {
  return (
    <AuthorisedImage
      path={path}
      className="h-32 w-full rounded border bg-white object-contain"
    />
  );
}

function SheetCard({ bidId, sheet }: { bidId: string; sheet: Sheet }) {
  const size =
    sheet.width_mm && sheet.height_mm
      ? `${Math.round(sheet.width_mm)} × ${Math.round(sheet.height_mm)} mm`
      : "size unknown";
  return (
    <li className="rounded-lg border">
      <Link
        to={`/bids/${bidId}/sheets/${sheet.id}`}
        className="block space-y-2 p-3 hover:bg-accent"
      >
        {sheet.has_thumbnail ? (
          <Thumbnail
            path={`/api/bids/${bidId}/sheets/${sheet.id}/thumbnail.webp`}
          />
        ) : (
          <div className="flex h-32 items-center justify-center rounded border text-xs text-muted-foreground">
            No preview yet
          </div>
        )}
        <div>
          <p className="truncate text-sm font-medium">
            {sheet.filename || "Sheet"}
          </p>
          <p className="text-xs text-muted-foreground">
            {sheet.layout_name ? `${sheet.layout_name} · ` : ""}
            {size} · {sheet.content_class ?? "unclassified"}
          </p>
        </div>
      </Link>
    </li>
  );
}

export function DocumentsPage() {
  const { bidId = "" } = useParams();
  const progress = useProgress(bidId);
  const sheets = useSheets(bidId, progress.data?.sheets);
  const upload = useUpload(bidId);
  const fileInput = useRef<HTMLInputElement>(null);
  const [chosen, setChosen] = useState(0);
  const [addendumId, setAddendumId] = useState("");

  return (
    <section className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">
          Tender documents
        </h1>
        <p className="text-sm text-muted-foreground">
          Drawings, specifications and schedules. A whole set can be sent as one
          zip, or as the folder it sits in.
        </p>
      </div>

      <form
        className="flex flex-wrap items-center gap-3 rounded-lg border p-4"
        onSubmit={(event) => {
          event.preventDefault();
          const files = fileInput.current?.files;
          if (files && files.length > 0) upload.mutate({ files, addendumId });
        }}
      >
        <input
          ref={fileInput}
          type="file"
          multiple
          aria-label="Files to upload"
          className="text-sm"
          onChange={(event) => setChosen(event.target.files?.length ?? 0)}
        />
        <AddendumChoice
          bidId={bidId}
          value={addendumId}
          onChange={setAddendumId}
        />
        <Button type="submit" disabled={chosen === 0 || upload.isPending}>
          {upload.isPending ? "Sending…" : "Upload"}
        </Button>
        {upload.isError && (
          <p role="alert" className="text-sm text-destructive">
            {upload.error.message}
          </p>
        )}
      </form>

      <FolderUpload bidId={bidId} />

      {progress.isPending && (
        <p role="status" className="text-sm text-muted-foreground">
          Loading the tender set…
        </p>
      )}

      {progress.data && (
        <div className="space-y-4">
          <Counts progress={progress.data} />
          {progress.data.total === 0 ? (
            <p className="text-sm text-muted-foreground">
              Nothing has been sent for this bid yet.
            </p>
          ) : (
            <p role="status" className="text-sm text-muted-foreground">
              {progress.data.finished
                ? `${progress.data.sheets} sheet${progress.data.sheets === 1 ? "" : "s"} ready to open.`
                : progress.data.sheets > 0
                  ? `Reading the set: ${progress.data.sheets_parsed ?? 0} of ${progress.data.sheets} sheets read…`
                  : "Reading the set…"}
            </p>
          )}

          {progress.data.failures.length > 0 && (
            <div className="space-y-2 rounded-lg border border-amber-300 p-4 dark:border-amber-700">
              <h2 className="text-sm font-semibold">
                {progress.data.failures.length === 1
                  ? "1 file needs attention"
                  : `${progress.data.failures.length} files need attention`}
              </h2>
              <ul className="space-y-2">
                {progress.data.failures.map((failure) => (
                  <li key={failure.id} className="text-sm">
                    <span className="font-medium">{failure.filename}</span>{" "}
                    <span className={TONE_CLASSES.bad}>
                      {STATE_LABELS[failure.state]?.label ?? failure.state}
                    </span>
                    {failure.reason && (
                      <p className="text-xs text-muted-foreground">
                        {failure.reason}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <ReadAgain bidId={bidId} progress={progress.data} />
        </div>
      )}

      {sheets.data && sheets.data.length > 0 && (
        <div className="space-y-3">
          <h2 className="text-lg font-semibold">Sheets</h2>
          <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {sheets.data.map((sheet) => (
              <SheetCard key={sheet.id} bidId={bidId} sheet={sheet} />
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

type Addendum = { id: string; number: string; issued_on: string | null };

/**
 * Which issue an upload belongs to: the original tender set, or an addendum (FR-DOC-05).
 * A new addendum is registered here, with its number and date, before its files are sent.
 */
function AddendumChoice({
  bidId,
  value,
  onChange,
}: {
  bidId: string;
  value: string;
  onChange: (id: string) => void;
}) {
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [number, setNumber] = useState("");
  const [issuedOn, setIssuedOn] = useState("");
  const addenda = useQuery({
    queryKey: ["addenda", bidId],
    queryFn: async (): Promise<Addendum[]> => {
      const { data, error } = await api.GET("/bids/{bid_id}/addenda", {
        params: { path: { bid_id: bidId } },
      });
      if (error || !data) throw new Error("Could not load the addenda");
      return data;
    },
  });
  const create = useMutation({
    mutationFn: async () => {
      const { data, error } = await api.POST("/bids/{bid_id}/addenda", {
        params: { path: { bid_id: bidId } },
        body: { number, issued_on: issuedOn || null },
      });
      if (error || !data)
        throw new Error(apiErrorMessage(error, "Could not add the addendum"));
      return data;
    },
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ["addenda", bidId] });
      onChange(created.id);
      setAdding(false);
      setNumber("");
      setIssuedOn("");
    },
  });

  if (adding) {
    return (
      <span className="inline-flex flex-wrap items-center gap-2 text-sm">
        <input
          aria-label="Addendum number"
          placeholder="Addendum no."
          className="w-28 rounded-md border bg-background px-2 py-1"
          value={number}
          onChange={(event) => setNumber(event.target.value)}
        />
        <input
          aria-label="Addendum date"
          type="date"
          className="rounded-md border bg-background px-2 py-1"
          value={issuedOn}
          onChange={(event) => setIssuedOn(event.target.value)}
        />
        <Button
          type="button"
          variant="outline"
          disabled={!number || create.isPending}
          onClick={() => create.mutate()}
        >
          Add addendum
        </Button>
        {create.isError && (
          <span className="text-destructive">{create.error.message}</span>
        )}
      </span>
    );
  }
  return (
    <select
      aria-label="This upload is"
      className="rounded-md border bg-background px-2 py-1 text-sm"
      value={value}
      onChange={(event) => {
        if (event.target.value === "new") setAdding(true);
        else onChange(event.target.value);
      }}
    >
      <option value="">The original tender set</option>
      {(addenda.data ?? []).map((addendum) => (
        <option key={addendum.id} value={addendum.id}>
          Addendum {addendum.number}
        </option>
      ))}
      <option value="new">A new addendum…</option>
    </select>
  );
}
