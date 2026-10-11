import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, apiErrorMessage } from "@/api/client";
import type { components } from "@/api/schema";
import { accessToken } from "@/auth/oidc";
import { useAuth } from "@/auth/session";
import { Button } from "@/components/ui/button";
import { ExchangeRates } from "@/pages/ExchangeRates";
import { LibraryProposals } from "@/pages/LibraryProposals";
import { ProductivityLibrary } from "@/pages/ProductivityLibrary";

/**
 * The company rate library (FR-CST-01).
 *
 * Every rate has a source (company standard, purchase order or quotation), a reference, the
 * date it took effect and how long it is valid. A new list is imported from a workbook, all
 * or nothing: any problem is reported by row and column and nothing changes. A changed rate
 * becomes a new version; the old one is kept, with every line priced from it.
 */

type Rate = components["schemas"]["RateOut"];
type Report = components["schemas"]["ImportOut"];

const EDITORS = ["senior_estimator"];
const SOURCES: Record<string, string> = {
  company_standard: "Company standard",
  purchase_order: "Purchase order",
  quotation: "Quotation",
};

export function RatesPage() {
  const { session } = useAuth();
  const canImport = session?.roles.some((role) => EDITORS.includes(role)) ?? false;
  const [open, setOpen] = useState<Rate | null>(null);
  const rates = useQuery({
    queryKey: ["rates"],
    queryFn: async (): Promise<Rate[]> => {
      const { data } = await api.GET("/rates");
      if (!data) throw new Error("Could not read the rate library");
      return data;
    },
  });

  return (
    <section className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Rate library</h1>
        <p className="text-sm text-muted-foreground">
          The company&apos;s unit rates, each with its source, date and validity. BOQ lines are
          priced only from these.
        </p>
      </div>

      {canImport && <ImportForm />}

      {rates.isLoading && <p className="text-sm">Reading…</p>}
      {rates.data && rates.data.length === 0 && (
        <p className="text-sm text-muted-foreground">The library is empty.</p>
      )}
      <div className="grid gap-6 lg:grid-cols-[1fr_22rem]">
        {rates.data && rates.data.length > 0 && (
          <table className="w-full text-sm" aria-label="Rates">
            <thead className="text-left text-xs text-muted-foreground">
              <tr>
                <th className="py-2 pr-3">Item</th>
                <th className="py-2 pr-3">Description</th>
                <th className="py-2 pr-3">Unit</th>
                <th className="py-2 pr-3 text-right">Rate (SGD)</th>
                <th className="py-2 pr-3">Source</th>
                <th className="py-2 pr-3">Effective</th>
                <th className="py-2 pr-3">Valid until</th>
                <th className="py-2" />
              </tr>
            </thead>
            <tbody>
              {rates.data.map((rate) => (
                <tr key={rate.id} className="border-t align-top">
                  <td className="py-2 pr-3">{rate.label}</td>
                  <td className="py-2 pr-3">{rate.description}</td>
                  <td className="py-2 pr-3">{rate.unit}</td>
                  <td className="py-2 pr-3 text-right">{rate.unit_rate}</td>
                  <td className="py-2 pr-3">
                    {SOURCES[rate.source_type] ?? rate.source_type}: {rate.source_reference}
                  </td>
                  <td className="py-2 pr-3">{rate.effective_from}</td>
                  <td className="py-2 pr-3">{rate.valid_until ?? "–"}</td>
                  <td className="py-2">
                    {rate.version > 1 && (
                      <Button variant="outline" onClick={() => setOpen(rate)}>
                        v{rate.version}: history
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {open && <History rate={open} onClose={() => setOpen(null)} />}
      </div>

      <LibraryProposals canDecide={canImport} />

      <ProductivityLibrary canImport={canImport} />

      <ExchangeRates canRecord={canImport} />
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
      const response = await fetch(new URL("/api/rates/import", window.location.origin), {
        method: "POST",
        headers: token ? { authorization: `Bearer ${token}` } : {},
        body,
      });
      const json = await response.json().catch(() => null);
      if (!response.ok) throw new Error(apiErrorMessage(json, "The import failed"));
      return json as Report;
    },
    onSuccess: (found) => {
      setError(null);
      setReport(found);
      void client.invalidateQueries({ queryKey: ["rates"] });
    },
    onError: (caught) => setError(caught instanceof Error ? caught.message : "The import failed"),
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
        Import a rate list (.xlsx)
        <input
          type="file"
          accept=".xlsx"
          className="mt-1 block text-sm"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
      </label>
      <p className="text-xs text-muted-foreground">
        Columns: type, DN, material, schedule, joining, brand, description, unit, rate, source
        type, source reference, effective from, valid until. Any problem imports nothing.
      </p>
      <Button type="submit" disabled={!file || upload.isPending}>
        Import
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
          <table className="text-xs" aria-label="Import problems">
            <tbody>
              {report.problems.map((problem, index) => (
                <tr key={index}>
                  <td className="pr-3">row {problem.row}</td>
                  <td className="pr-3">{problem.column}</td>
                  <td>{problem.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </form>
  );
}

function History({ rate, onClose }: { rate: Rate; onClose: () => void }) {
  const versions = useQuery({
    queryKey: ["rates", rate.id, "history"],
    queryFn: async (): Promise<Rate[]> => {
      const { data } = await api.GET("/rates/{rate_id}/history", {
        params: { path: { rate_id: rate.id } },
      });
      if (!data) throw new Error("Could not read the history");
      return data;
    },
  });
  return (
    <aside aria-label="Rate history" className="space-y-2 rounded-md border p-3 text-sm">
      <div className="flex items-center justify-between">
        <p className="font-medium">{rate.label}</p>
        <Button variant="outline" onClick={onClose}>
          Close
        </Button>
      </div>
      <ol className="space-y-1">
        {(versions.data ?? []).map((version) => (
          <li key={version.id}>
            v{version.version}: SGD {version.unit_rate} from {version.effective_from}
            {version.retired_at ? " (replaced)" : " (current)"}
          </li>
        ))}
      </ol>
    </aside>
  );
}
