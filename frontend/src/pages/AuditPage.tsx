import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api } from "@/api/client";
import type { components } from "@/api/schema";
import { Button } from "@/components/ui/button";

type AuditEvent = components["schemas"]["AuditEventOut"];

interface Filters {
  action: string;
  entity_type: string;
  occurred_from: string;
  occurred_to: string;
}

const EMPTY: Filters = { action: "", entity_type: "", occurred_from: "", occurred_to: "" };

const fieldClass =
  "w-full rounded-md border bg-background px-3 py-2 text-sm " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

/** Only the filters that were filled in; an empty box means "no filter", not "match empty". */
function toQuery(filters: Filters): Record<string, string> {
  return Object.fromEntries(
    Object.entries(filters)
      .filter(([, value]) => value !== "")
      .map(([key, value]) =>
        key.startsWith("occurred_") ? [key, new Date(value).toISOString()] : [key, value],
      ),
  );
}

function useAuditPage(filters: Filters, cursor: string | null) {
  return useQuery({
    queryKey: ["audit", filters, cursor],
    queryFn: async () => {
      const { data, error } = await api.GET("/audit", {
        params: { query: { ...toQuery(filters), ...(cursor ? { cursor } : {}) } },
      });
      if (error || !data) throw new Error("Could not load the history");
      return data;
    },
  });
}

function EventRow({ event }: { event: AuditEvent }) {
  return (
    <tr className="border-b last:border-0 align-top">
      <td className="py-2 pr-4 whitespace-nowrap">
        {new Date(event.occurred_at).toLocaleString("en-SG")}
      </td>
      <td className="py-2 pr-4">{event.actor_label}</td>
      <td className="py-2 pr-4">{event.action}</td>
      <td className="py-2 pr-4">
        {event.entity_type}
        <div className="text-xs text-muted-foreground">{event.entity_id}</div>
      </td>
      <td className="py-2 text-muted-foreground">{event.reason ?? ""}</td>
    </tr>
  );
}

export function AuditPage() {
  const [draft, setDraft] = useState<Filters>(EMPTY);
  const [filters, setFilters] = useState<Filters>(EMPTY);
  const [cursor, setCursor] = useState<string | null>(null);
  const page = useAuditPage(filters, cursor);

  const update =
    (field: keyof Filters) => (event: React.ChangeEvent<HTMLInputElement>) =>
      setDraft((current) => ({ ...current, [field]: event.target.value }));

  const exportUrl = `/api/audit/export.csv?${new URLSearchParams(toQuery(filters)).toString()}`;

  return (
    <section className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">History</h1>
        <p className="text-sm text-muted-foreground">
          Every recorded action, newest first. The record cannot be edited or deleted.
        </p>
      </div>

      <form
        className="grid gap-4 sm:grid-cols-4"
        onSubmit={(event) => {
          event.preventDefault();
          setCursor(null);
          setFilters(draft);
        }}
      >
        <div className="space-y-1">
          <label htmlFor="action" className="text-sm font-medium">
            Action contains
          </label>
          <input id="action" className={fieldClass} value={draft.action} onChange={update("action")} />
        </div>
        <div className="space-y-1">
          <label htmlFor="entity_type" className="text-sm font-medium">
            Entity
          </label>
          <input
            id="entity_type"
            className={fieldClass}
            value={draft.entity_type}
            onChange={update("entity_type")}
          />
        </div>
        <div className="space-y-1">
          <label htmlFor="occurred_from" className="text-sm font-medium">
            From
          </label>
          <input
            id="occurred_from"
            type="datetime-local"
            className={fieldClass}
            value={draft.occurred_from}
            onChange={update("occurred_from")}
          />
        </div>
        <div className="space-y-1">
          <label htmlFor="occurred_to" className="text-sm font-medium">
            To
          </label>
          <input
            id="occurred_to"
            type="datetime-local"
            className={fieldClass}
            value={draft.occurred_to}
            onChange={update("occurred_to")}
          />
        </div>
        <div className="flex gap-3 sm:col-span-4">
          <Button type="submit">Apply filters</Button>
          <Button type="button" variant="outline" asChild>
            <a href={exportUrl} download>
              Export CSV
            </a>
          </Button>
        </div>
      </form>

      {page.isPending && (
        <p role="status" className="text-sm text-muted-foreground">
          Loading the history…
        </p>
      )}
      {page.isError && (
        <p role="alert" className="text-sm text-destructive">
          Could not load the history
        </p>
      )}
      {page.isSuccess && (
        <>
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Recorded actions</caption>
            <thead className="border-b text-xs uppercase tracking-wide text-muted-foreground">
              <tr>
                <th scope="col" className="py-2 pr-4 font-medium">
                  When
                </th>
                <th scope="col" className="py-2 pr-4 font-medium">
                  Who
                </th>
                <th scope="col" className="py-2 pr-4 font-medium">
                  Action
                </th>
                <th scope="col" className="py-2 pr-4 font-medium">
                  Entity
                </th>
                <th scope="col" className="py-2 font-medium">
                  Reason
                </th>
              </tr>
            </thead>
            <tbody>
              {page.data.items.map((event) => (
                <EventRow key={event.id} event={event} />
              ))}
            </tbody>
          </table>
          {page.data.items.length === 0 && (
            <p className="text-sm text-muted-foreground">Nothing matches those filters.</p>
          )}
          {page.data.next_cursor && (
            <Button variant="outline" onClick={() => setCursor(page.data.next_cursor)}>
              Show older
            </Button>
          )}
        </>
      )}
    </section>
  );
}
