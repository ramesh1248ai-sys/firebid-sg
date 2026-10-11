import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, apiErrorMessage } from "@/api/client";
import type { components } from "@/api/schema";
import { Button } from "@/components/ui/button";

/**
 * Exchange rates (FR-CST-04).
 *
 * A quotation in another currency is brought to SGD at a recorded rate, with the buffer and
 * the import lines the company's configuration sets. A rate is recorded with where it came
 * from and the day it is of; a quotation in a currency with no rate cannot be confirmed.
 * A new rate for a currency does not replace the old one: each is kept.
 */

type Table = components["schemas"]["FxTableOut"];

const EMPTY = { currency: "", rate: "", source: "", as_of: "" };

export function ExchangeRates({ canRecord }: { canRecord: boolean }) {
  const client = useQueryClient();
  const [form, setForm] = useState(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const table = useQuery({
    queryKey: ["fx-rates"],
    queryFn: async (): Promise<Table> => {
      const { data } = await api.GET("/costing/fx-rates");
      if (!data || !Array.isArray(data.rates)) throw new Error("Could not read the rates");
      return data;
    },
  });
  const record = useMutation({
    mutationFn: async (): Promise<Table> => {
      const { data, error: refused } = await api.POST("/costing/fx-rates", {
        body: {
          currency: form.currency.trim().toUpperCase(),
          rate: form.rate.trim(),
          source: form.source.trim(),
          as_of: form.as_of,
        },
      });
      if (refused || !data) throw new Error(apiErrorMessage(refused, "The rate was not recorded"));
      return data;
    },
    onSuccess: (data) => {
      setForm(EMPTY);
      setError(null);
      client.setQueryData(["fx-rates"], data);
      // A quotation waiting on this currency can now be priced.
      void client.invalidateQueries({ queryKey: ["boq"] });
    },
    onError: (caught) =>
      setError(caught instanceof Error ? caught.message : "The rate was not recorded"),
  });
  const set = (name: keyof typeof EMPTY) => (event: { target: { value: string } }) =>
    setForm((current) => ({ ...current, [name]: event.target.value }));
  const ready =
    form.currency.trim().length === 3 &&
    Number(form.rate) > 0 &&
    form.source.trim() !== "" &&
    form.as_of !== "";

  return (
    <section aria-label="Exchange rates" className="space-y-4">
      <div>
        <h2 className="text-lg font-medium">Exchange rates</h2>
        <p className="text-sm text-muted-foreground">
          What a quotation in another currency is brought to SGD at
          {table.data ? `, with a buffer of ${table.data.buffer_percent}% on the rate` : ""}. A
          quotation in a currency with no rate here cannot be confirmed.
        </p>
      </div>

      {canRecord && (
        <form
          aria-label="Record an exchange rate"
          className="flex flex-wrap items-end gap-3 rounded-md border p-3 text-sm"
          onSubmit={(event) => {
            event.preventDefault();
            record.mutate();
          }}
        >
          <label>
            <span className="block text-xs text-muted-foreground">Currency</span>
            <input
              aria-label="Currency"
              className="w-20 uppercase"
              maxLength={3}
              placeholder="USD"
              value={form.currency}
              onChange={set("currency")}
            />
          </label>
          <label>
            <span className="block text-xs text-muted-foreground">SGD for one unit</span>
            <input
              aria-label="SGD for one unit"
              className="w-28"
              inputMode="decimal"
              placeholder="1.3450"
              value={form.rate}
              onChange={set("rate")}
            />
          </label>
          <label>
            <span className="block text-xs text-muted-foreground">Rate of (date)</span>
            <input aria-label="Rate date" type="date" value={form.as_of} onChange={set("as_of")} />
          </label>
          <label className="grow">
            <span className="block text-xs text-muted-foreground">
              Source: where the rate is from
            </span>
            <input
              aria-label="Source of the rate"
              className="w-full"
              placeholder="MAS, or the bank's quoted rate"
              value={form.source}
              onChange={set("source")}
            />
          </label>
          <Button type="submit" disabled={!ready || record.isPending}>
            Record the rate
          </Button>
          {error && (
            <p role="alert" className="basis-full text-sm text-red-700">
              {error}
            </p>
          )}
        </form>
      )}

      {table.data && table.data.rates.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No rate is recorded: a quotation in SGD needs none.
        </p>
      )}
      {table.data && table.data.rates.length > 0 && (
        <table className="w-full max-w-2xl text-sm" aria-label="Exchange rates">
          <thead className="text-left text-xs text-muted-foreground">
            <tr>
              <th className="py-2 pr-3">Currency</th>
              <th className="py-2 pr-3 text-right">SGD for one unit</th>
              <th className="py-2 pr-3">Rate of</th>
              <th className="py-2 pr-3">Source</th>
            </tr>
          </thead>
          <tbody>
            {table.data.rates.map((rate) => (
              <tr key={rate.id} className="border-t">
                <td className="py-2 pr-3">{rate.currency}</td>
                <td className="py-2 pr-3 text-right">{rate.rate}</td>
                <td className="py-2 pr-3">{rate.as_of}</td>
                <td className="py-2 pr-3">{rate.source}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
