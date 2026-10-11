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
 * nothing, as a rate list is; one figure is entered by hand, with its source.
 */

type Entry = components["schemas"]["EntryOut"];
type Report = components["schemas"]["ProductivityImportOut"];
type NewEntry = components["schemas"]["EntryIn"];

const SOURCES: { value: NewEntry["source_type"]; label: string }[] = [
  { value: "company_standard", label: "Company standard" },
  { value: "historical_project", label: "Historical project" },
  { value: "estimator_judgement", label: "Estimator judgement" },
];

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
      {canImport && <EntryForm />}

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

const EMPTY = {
  item_type: "",
  dn: "",
  joining: "",
  description: "",
  unit: "",
  hours_per_unit: "",
  trade: "",
  source_type: "company_standard" as NewEntry["source_type"],
  source_reference: "",
};

/** One figure, entered by hand. A figure with the same item and unit as one in the library
 * becomes its new version; the old one is kept. */
function EntryForm() {
  const client = useQueryClient();
  const [form, setForm] = useState(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<Entry | null>(null);
  const set = (name: keyof typeof EMPTY) => (event: { target: { value: string } }) =>
    setForm((current) => ({ ...current, [name]: event.target.value }));
  const judgement = form.source_type === "estimator_judgement";
  const save = useMutation({
    mutationFn: async (): Promise<Entry> => {
      const { data, error: refused } = await api.POST("/labour/productivity", {
        body: {
          item_type: form.item_type.trim(),
          dn: form.dn.trim(),
          joining: form.joining.trim(),
          description: form.description.trim(),
          unit: form.unit.trim(),
          hours_per_unit: form.hours_per_unit.trim(),
          trade: form.trade.trim(),
          source_type: form.source_type,
          source_reference: form.source_reference.trim() || null,
        },
      });
      if (refused || !data) throw new Error(apiErrorMessage(refused, "The figure was not saved"));
      return data;
    },
    onSuccess: (entry) => {
      setError(null);
      setSaved(entry);
      setForm(EMPTY);
      void client.invalidateQueries({ queryKey: ["productivity"] });
      void client.invalidateQueries({ queryKey: ["boq"] });
    },
    onError: (caught) => {
      setSaved(null);
      setError(caught instanceof Error ? caught.message : "The figure was not saved");
    },
  });
  const ready =
    form.item_type.trim() &&
    form.description.trim() &&
    form.unit.trim() &&
    form.trade.trim() &&
    Number(form.hours_per_unit) > 0 &&
    (judgement || form.source_reference.trim());

  return (
    <details className="rounded-md border p-3">
      <summary className="cursor-pointer text-sm font-medium">Enter one figure by hand</summary>
      <form
        aria-label="One productivity figure"
        className="mt-3 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-3"
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate();
        }}
      >
        <label>
          <span className="block text-xs text-muted-foreground">Type (as the takeoff names it)</span>
          <input
            aria-label="Type"
            className="w-full"
            placeholder="pipe"
            value={form.item_type}
            onChange={set("item_type")}
          />
        </label>
        <label>
          <span className="block text-xs text-muted-foreground">DN (blank for any size)</span>
          <input aria-label="DN" className="w-full" value={form.dn} onChange={set("dn")} />
        </label>
        <label>
          <span className="block text-xs text-muted-foreground">Joining (blank for any)</span>
          <input
            aria-label="Joining"
            className="w-full"
            value={form.joining}
            onChange={set("joining")}
          />
        </label>
        <label className="sm:col-span-2">
          <span className="block text-xs text-muted-foreground">Description</span>
          <input
            aria-label="Description"
            className="w-full"
            value={form.description}
            onChange={set("description")}
          />
        </label>
        <label>
          <span className="block text-xs text-muted-foreground">Trade</span>
          <input
            aria-label="Trade"
            className="w-full"
            placeholder="pipefitter"
            value={form.trade}
            onChange={set("trade")}
          />
        </label>
        <label>
          <span className="block text-xs text-muted-foreground">Man-hours</span>
          <input
            aria-label="Man-hours per unit"
            className="w-full"
            inputMode="decimal"
            value={form.hours_per_unit}
            onChange={set("hours_per_unit")}
          />
        </label>
        <label>
          <span className="block text-xs text-muted-foreground">Per (unit)</span>
          <input
            aria-label="Unit"
            className="w-full"
            placeholder="m"
            value={form.unit}
            onChange={set("unit")}
          />
        </label>
        <label>
          <span className="block text-xs text-muted-foreground">Source</span>
          <select
            aria-label="Source type"
            className="w-full"
            value={form.source_type}
            onChange={(event) =>
              setForm((current) => ({
                ...current,
                source_type: event.target.value as NewEntry["source_type"],
              }))
            }
          >
            {SOURCES.map((one) => (
              <option key={one.value} value={one.value}>
                {one.label}
              </option>
            ))}
          </select>
        </label>
        <label className="sm:col-span-2">
          <span className="block text-xs text-muted-foreground">
            {judgement
              ? "Source reference (left blank, it is under your name)"
              : "Source reference: the standard, or the project it was measured on"}
          </span>
          <input
            aria-label="Source reference"
            className="w-full"
            value={form.source_reference}
            onChange={set("source_reference")}
          />
        </label>
        <div className="flex items-end">
          <Button type="submit" disabled={!ready || save.isPending}>
            Save the figure
          </Button>
        </div>
        {error && (
          <p role="alert" className="text-sm text-red-700 sm:col-span-2 lg:col-span-3">
            {error}
          </p>
        )}
        {saved && (
          <p role="status" className="text-sm sm:col-span-2 lg:col-span-3">
            Saved: {saved.description}, {saved.hours_per_unit} h per {saved.unit} ({saved.source})
            {saved.version > 1 ? `, version ${saved.version}` : ""}.
          </p>
        )}
      </form>
    </details>
  );
}
