import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";

import { api, apiErrorMessage } from "@/api/client";
import type { components } from "@/api/schema";
import { accessToken } from "@/auth/oidc";
import { Button } from "@/components/ui/button";

/**
 * Sending a whole folder (FR-DOC-09), and saying whose documents are in it (FR-DOC-10).
 *
 * A tender folder on an estimator's drive mixes what the client issued with the company's own
 * marked-up drawings and earlier responses. Before anything is sent, each folder is shown with
 * the origin the platform proposes for it, and the person confirms or changes it. Only tender
 * documents are read; working and reference documents are kept, unread. The files then go in
 * batches, so a dropped connection costs one batch, and every file is accounted for at the end.
 */

type Origin = "tender" | "working" | "reference" | "ignored";
type Report = components["schemas"]["UploadReport"];
type DocumentOut = components["schemas"]["DocumentOut"];

const ORIGIN_LABEL: Record<Origin, string> = {
  tender: "Tender documents (read)",
  working: "Our working documents (kept, not read)",
  reference: "Reference (kept, not read)",
  ignored: "Leave out",
};

// The API takes files up to 200 MB each; a batch stays well under the web server's limit.
const MAX_FILE_BYTES = 200 * 1024 * 1024;
const BATCH_BYTES = 150 * 1024 * 1024;
const BATCH_FILES = 40;

type Chosen = { file: File; path: string; folder: string; proposed: Origin; reason: string };
type Group = { folder: string; files: Chosen[]; proposed: Origin; reason: string };
type Outcome = {
  sent: number;
  stored: number;
  duplicates: number;
  ignored: number;
  refused: { filename: string; reason: string }[];
};

function pathOf(file: File): string {
  return file.webkitRelativePath || file.name;
}

function folderOf(path: string): string {
  const cut = path.lastIndexOf("/");
  return cut < 0 ? "(the folder itself)" : path.slice(0, cut);
}

/** Folders whose files are all proposed the same way, so a person decides once for each. */
function group(files: Chosen[]): Group[] {
  const groups = new Map<string, Group>();
  for (const chosen of files) {
    const key = `${chosen.folder}\u0000${chosen.proposed}`;
    const found = groups.get(key);
    if (found) found.files.push(chosen);
    else
      groups.set(key, { folder: chosen.folder, files: [chosen], proposed: chosen.proposed, reason: chosen.reason });
  }
  return [...groups.values()].sort((a, b) => a.folder.localeCompare(b.folder));
}

function batches(files: { chosen: Chosen; origin: Origin }[]) {
  const out: { chosen: Chosen; origin: Origin }[][] = [];
  let current: { chosen: Chosen; origin: Origin }[] = [];
  let bytes = 0;
  for (const item of files) {
    if (current.length >= BATCH_FILES || (current.length > 0 && bytes + item.chosen.file.size > BATCH_BYTES)) {
      out.push(current);
      current = [];
      bytes = 0;
    }
    current.push(item);
    bytes += item.chosen.file.size;
  }
  if (current.length > 0) out.push(current);
  return out;
}

export function FolderUpload({ bidId }: { bidId: string }) {
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [chosen, setChosen] = useState<Chosen[]>([]);
  const [decided, setDecided] = useState<Record<string, Origin>>({});
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const groups = group(chosen);
  const keyOf = (item: Group) => `${item.folder}\u0000${item.proposed}`;
  const originOf = (item: Group): Origin => decided[keyOf(item)] ?? item.proposed;

  const propose = useMutation({
    mutationFn: async (files: File[]): Promise<Chosen[]> => {
      const paths = files.map(pathOf);
      const { data, error } = await api.POST("/bids/{bid_id}/documents/origins", {
        params: { path: { bid_id: bidId } },
        body: paths,
      });
      if (error || !data) throw new Error(apiErrorMessage(error, "Could not read the folder's contents"));
      return files.map((file, index) => ({
        file,
        path: paths[index] ?? file.name,
        folder: folderOf(paths[index] ?? file.name),
        proposed: (data[index]?.origin ?? "tender") as Origin,
        reason: data[index]?.reason ?? "",
      }));
    },
    onSuccess: (files) => {
      setChosen(files);
      setDecided({});
      setOutcome(null);
    },
  });

  const send = useMutation({
    mutationFn: async (): Promise<Outcome> => {
      const result: Outcome = { sent: 0, stored: 0, duplicates: 0, ignored: 0, refused: [] };
      const sending: { chosen: Chosen; origin: Origin }[] = [];
      for (const item of groups) {
        const origin = originOf(item);
        for (const file of item.files) {
          if (origin === "ignored") result.ignored += 1;
          else if (file.file.size > MAX_FILE_BYTES)
            result.refused.push({
              filename: file.path,
              reason: "larger than 200 MB: too large to send. If it is an archive of this folder, it is not needed.",
            });
          else sending.push({ chosen: file, origin });
        }
      }
      setProgress({ done: 0, total: sending.length });
      const token = await accessToken();
      for (const batch of batches(sending)) {
        const body = new FormData();
        for (const { chosen: file, origin } of batch) {
          body.append("files", file.file, file.file.name);
          body.append("paths", file.path);
          body.append("origins", origin);
        }
        const response = await fetch(new URL(`/api/bids/${bidId}/documents`, window.location.origin), {
          method: "POST",
          headers: token ? { authorization: `Bearer ${token}` } : {},
          body,
        });
        if (!response.ok) {
          // This batch failed whole; the others still go, and these files are named.
          const reason = apiErrorMessage(await response.json().catch(() => null), `the server answered ${response.status}`);
          for (const { chosen: file } of batch) result.refused.push({ filename: file.path, reason });
        } else {
          const report = (await response.json()) as Report;
          result.stored += report.stored?.length ?? 0;
          result.duplicates += report.duplicates?.length ?? 0;
          result.ignored += report.ignored?.length ?? 0;
          for (const refused of [...(report.rejected ?? []), ...(report.quarantined ?? []), ...(report.awaiting_scan ?? [])])
            result.refused.push(refused);
        }
        result.sent += batch.length;
        setProgress({ done: result.sent, total: sending.length });
        queryClient.invalidateQueries({ queryKey: ["progress", bidId] });
      }
      return result;
    },
    onSuccess: (result) => {
      setOutcome(result);
      setChosen([]);
      setProgress(null);
      if (input.current) input.current.value = "";
      queryClient.invalidateQueries({ queryKey: ["documents", bidId] });
      queryClient.invalidateQueries({ queryKey: ["sheets", bidId] });
    },
  });

  const counted = (origin: Origin) =>
    groups.filter((item) => originOf(item) === origin).reduce((sum, item) => sum + item.files.length, 0);

  return (
    <div className="space-y-3 rounded-lg border p-4">
      <div className="flex flex-wrap items-center gap-3">
        <label className="text-sm font-medium" htmlFor="folder-input">
          Or send a whole folder
        </label>
        <input
          id="folder-input"
          ref={(element) => {
            input.current = element;
            // Not in React's typings: the attribute that makes a file input pick a folder.
            element?.setAttribute("webkitdirectory", "");
          }}
          type="file"
          multiple
          aria-label="Folder to upload"
          className="text-sm"
          onChange={(event) => {
            const files = [...(event.target.files ?? [])];
            if (files.length > 0) propose.mutate(files);
          }}
        />
      </div>
      {propose.isPending && <p className="text-sm text-muted-foreground">Reading the folder…</p>}
      {propose.isError && (
        <p role="alert" className="text-sm text-destructive">
          {propose.error.message}
        </p>
      )}
      {groups.length > 0 && (
        <>
          <p className="text-sm text-muted-foreground">
            Say whose documents each folder holds. Only tender documents are read and taken off; the others are kept
            with the bid, unread.
          </p>
          <table className="w-full text-sm" aria-label="Folders to send">
            <thead className="text-left text-xs text-muted-foreground">
              <tr>
                <th className="py-1 pr-3">Folder</th>
                <th className="py-1 pr-3 text-right">Files</th>
                <th className="py-1 pr-3">These are</th>
                <th className="py-1 pr-3">Why proposed</th>
              </tr>
            </thead>
            <tbody>
              {groups.map((item) => (
                <tr key={keyOf(item)} className="border-t align-top">
                  <td className="py-1 pr-3 break-all">{item.folder}</td>
                  <td className="py-1 pr-3 text-right">{item.files.length}</td>
                  <td className="py-1 pr-3">
                    <select
                      aria-label={`Origin of ${item.folder}`}
                      className="rounded-md border bg-background px-2 py-1"
                      value={originOf(item)}
                      onChange={(event) => setDecided({ ...decided, [keyOf(item)]: event.target.value as Origin })}
                    >
                      {(Object.keys(ORIGIN_LABEL) as Origin[]).map((origin) => (
                        <option key={origin} value={origin}>
                          {ORIGIN_LABEL[origin]}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="py-1 pr-3 text-xs text-muted-foreground">{item.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="flex flex-wrap items-center gap-3">
            <Button type="button" onClick={() => send.mutate()} disabled={send.isPending}>
              {send.isPending && progress
                ? `Sending ${progress.done} of ${progress.total}…`
                : `Send ${chosen.length - counted("ignored")} files (${counted("tender")} to be read)`}
            </Button>
            {send.isError && (
              <p role="alert" className="text-sm text-destructive">
                {send.error.message}
              </p>
            )}
          </div>
        </>
      )}
      {outcome && (
        <div role="status" className="space-y-1 text-sm">
          <p>
            {outcome.stored} stored, {outcome.duplicates} already on the bid, {outcome.ignored} left out,{" "}
            {outcome.refused.length} not taken.
          </p>
          {outcome.refused.length > 0 && (
            <ul className="list-disc pl-5 text-xs" aria-label="Files not taken">
              {outcome.refused.map((refused, index) => (
                <li key={`${refused.filename}-${index}`}>
                  <span className="font-medium">{refused.filename}</span>: {refused.reason}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
      <OriginsToConfirm bidId={bidId} />
    </div>
  );
}

/** Documents whose origin was proposed as other than tender and nobody has confirmed. */
function OriginsToConfirm({ bidId }: { bidId: string }) {
  const queryClient = useQueryClient();
  const documents = useQuery({
    queryKey: ["documents", bidId],
    queryFn: async (): Promise<DocumentOut[]> => {
      const { data, error } = await api.GET("/bids/{bid_id}/documents", { params: { path: { bid_id: bidId } } });
      if (error || !data) throw new Error("Could not load the documents");
      return data;
    },
  });
  const decide = useMutation({
    mutationFn: async ({ ids, origin }: { ids: string[]; origin: "tender" | "working" | "reference" }) => {
      const { error } = await api.POST("/bids/{bid_id}/documents/origin", {
        params: { path: { bid_id: bidId } },
        body: { document_ids: ids, origin },
      });
      if (error) throw new Error(apiErrorMessage(error, "Could not set the origin"));
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["documents", bidId] });
      queryClient.invalidateQueries({ queryKey: ["progress", bidId] });
    },
  });
  const listed = Array.isArray(documents.data) ? documents.data : [];
  const waiting = listed.filter((document) => document.origin_status === "proposed");
  if (waiting.length === 0) return null;
  return (
    <div className="space-y-2 rounded-lg border border-amber-300 p-3 dark:border-amber-700">
      <h2 className="text-sm font-semibold">
        {waiting.length === 1 ? "1 document is" : `${waiting.length} documents are`} not being read until you say whose
        {waiting.length === 1 ? " it is" : " they are"}
      </h2>
      <ul className="space-y-2" aria-label="Origins to confirm">
        {waiting.map((document) => (
          <li key={document.id} className="text-sm">
            <span className="font-medium break-all">{document.source_path ?? document.filename}</span>
            <p className="text-xs text-muted-foreground">
              Proposed: {ORIGIN_LABEL[(document.origin ?? "tender") as Origin]}. {document.origin_reason}
            </p>
            <span className="inline-flex gap-2">
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={decide.isPending}
                onClick={() =>
                  decide.mutate({ ids: [document.id], origin: (document.origin ?? "working") as "working" | "reference" })
                }
              >
                Keep it unread
              </Button>
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={decide.isPending}
                onClick={() => decide.mutate({ ids: [document.id], origin: "tender" })}
              >
                It is a tender document: read it
              </Button>
            </span>
          </li>
        ))}
      </ul>
      {decide.isError && (
        <p role="alert" className="text-sm text-destructive">
          {decide.error.message}
        </p>
      )}
    </div>
  );
}
