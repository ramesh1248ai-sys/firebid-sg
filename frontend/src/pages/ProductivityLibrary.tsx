import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, apiErrorMessage } from "@/api/client";
import type { components } from "@/api/schema";
import { accessToken } from "@/auth/oidc";
import { Button } from "@/components/ui/button";

/**
 * The company productivity library (FR-LAB-01).
 *
 * Man-hours for a unit of each item, each with its source: a company standard, a project
 * it was measured on, or a named estimator's judgement. Labour is worked out only from
 * these, so a line with no entry has no hours. A list is imported from a workbook, all or
 * nothing, as a rate list is.
 */

type Entry = components["schemas"]["EntryOut"];
type Report = components["schemas"]["ProductivityImportOut"];

export function ProductivityLibrary({ canImport }: { canImport: boolean }) {
  const entries = useQuery({
    queryKey: ["productivity"],
    queryFn: async (): Promise<Entry[]> => {
      const { data } = await api.GET("/labour/productivity");
      if (!Array.isArray(data)) throw new Error("Could not read the productivity library");
      return data;
    },
  });

  return (
    <section aria-label="Productivity library" className="space-y-4">
      <div>
        <h2 className="text-lg font-medium">Productivity library</h2>
        <p className="text-sm text-muted-foreground">
          Man-hours for a unit of each item, each with its source. Labour is worked out only
          from these: a BOQ line with no entry here has no hours.
        </p>
      </div>

      {canImport && <ImportForm />}

      {entries.isLoading && <p className="text-sm">Reading…</p>}
      {entries.isError && (
        <p className="text-sm text-muted-foreground">
          The productivity library could not be read.
        </p>
      )}
      {entries.data && entries.data.length === 0 && (
        <p className="text-sm text-muted-foreground">
          The library is empty: no BOQ line has labour hours until a list is imported.
        </p>
      )}
      {entries.data && entries.data.length > 0 && (
        <table className="w-full text-sm" aria-label="Productivity">
          <thead className="text-left text-xs text-muted-foreground">
            <tr>
              <th className="py-2 pr-3">Item</th>
              <th className="py-2 pr-3">Description</th>
              <th className="py-2 pr-3 text-right">Man-hours</th>
              <th className="py-2 pr-3">Per</th>
              <th className="py-2 pr-3">Trade</th>
              <th className="py-2 pr-3">Source</th>
            </tr>
          </thead>
          <tbody>
            {entries.data.map((entry) => (
              <tr key={entry.id} className="border-t align-top">
                <td className="py-2 pr-3">
                  {entry.item_type.replaceAll("_", " ")}
                  {entry.dn ? `, DN${entry.dn}` : ""}
                  {entry.joining ? `, ${entry.joining}` : ""}
                </td>
                <td className="py-2 pr-3">{entry.description}</td>
                <td className="py-2 pr-3 text-right">{entry.hours_per_unit}</td>
                <td className="py-2 pr-3">{entry.unit}</td>
                <td className="py-2 pr-3">{entry.trade.replaceAll("_", " ")}</td>
                <td className="py-2 pr-3">
                  {entry.source}
                  {entry.version > 1 && (
                    <span className="ml-1 text-xs text-muted-foreground">v{entry.version}</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function ImportForm() {
  const client = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState<string | null>(null);
  const upload = useMutation({
    mutationFn: async (chosen: File): Promise<Report> => {
      const body = new FormData();
      body.append("file", chosen);
      const token = await accessToken();
      const response = await fetch(
        new URL("/api/labour/productivity/import", window.location.origin),
        { method: "POST", headers: token ? { authorization: `Bearer ${token}` } : {}, body },
      );
      const json = await response.json().catch(() => null);
      if (!response.ok) throw new Error(apiErrorMessage(json, "The import failed"));
      return json as Report;
    },
    onSuccess: (found) => {
      setError(null);
      setReport(found);
      void client.invalidateQueries({ queryKey: ["productivity"] });
      // Every bid's labour is worked out from the library.
      void client.invalidateQueries({ queryKey: ["boq"] });
    },
    onError: (caught) => {
      setReport(null);
      setError(caught instanceof Error ? caught.message : "The import failed");
    },
  });
  return (
    <form
      className="space-y-3 rounded-md border p-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (file) upload.mutate(file);
      }}
    >
      <label className="block text-sm font-medium">
        Import a productivity list (.xlsx)
        <input
          type="file"
          accept=".xlsx"
          className="mt-1 block text-sm"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
      </label>
      <p className="text-xs text-muted-foreground">
        Columns: type, DN, joining, description, unit, man-hours per unit, trade, source type,
        source reference. Any problem imports nothing.
      </p>
      <Button type="submit" disabled={!file || upload.isPending}>
        Import the productivity list
      </Button>
      {error && (
        <p role="alert" className="text-sm text-red-700">
          {error}
        </p>
      )}
      {report && report.imported && (
        <p role="status" className="text-sm">
          Imported: {report.created} new, {report.superseded} replaced by a new version,{" "}
          {report.unchanged} unchanged.
        </p>
      )}
      {report && !report.imported && (
        <div role="alert" className="space-y-1 text-sm">
          <p className="font-medium text-red-700">
            Nothing was imported. Fix these and import again:
          </p>
          <table className="text-xs" aria-label="Productivity import problems">
            <tbody>
              {report.problems.map((problem, index) => (
                <tr key={index}>
                  <td className="pr-3">row {String(problem.row)}</td>
                  <td className="pr-3">{String(problem.column)}</td>
                  <td>{String(problem.message)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </form>
  );
}
