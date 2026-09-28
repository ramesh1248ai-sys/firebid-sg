import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  type RowSelectionState,
  useReactTable,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useMemo, useRef, useState } from "react";

import { STATUS_COLOURS } from "./marks";
import type { QueueFilters, QueueRow, Reason } from "./review";

/**
 * The review queue (FR-REV-02): riskiest first, filterable, long lists virtualised so only
 * the rows on screen are rendered. Rows are selected with their checkboxes (or "select
 * page") for bulk accept and reject; clicking a row opens it.
 */

export const PAGE_SIZE = 25;

function statusOf(row: QueueRow): string {
  const item = row.item;
  if (item.manual && !["verified", "rejected"].includes(item.state)) return "manual";
  return item.state;
}

export function QueuePanel({
  rows,
  filters,
  onFilters,
  levels,
  types,
  openId,
  onOpen,
  selection,
  onSelection,
  reasons,
  onAccept,
  onReject,
  busy,
}: {
  rows: QueueRow[];
  filters: QueueFilters;
  onFilters: (filters: QueueFilters) => void;
  levels: string[];
  types: string[];
  openId: string | null;
  onOpen: (row: QueueRow) => void;
  selection: RowSelectionState;
  onSelection: (selection: RowSelectionState) => void;
  reasons: Reason[];
  onAccept: (ids: string[]) => void;
  onReject: (ids: string[], reason: string) => void;
  busy: boolean;
}) {
  const [reason, setReason] = useState("");
  const columns = useMemo<ColumnDef<QueueRow>[]>(
    () => [
      {
        id: "select",
        header: "",
        cell: ({ row }) => (
          <input
            type="checkbox"
            aria-label={`Select ${row.original.item.human_id}`}
            checked={row.getIsSelected()}
            onChange={row.getToggleSelectedHandler()}
            onClick={(event) => event.stopPropagation()}
          />
        ),
      },
      {
        id: "item",
        header: "Item",
        cell: ({ row }) => (
          <div className="min-w-0">
            <p className="truncate font-medium">{row.original.item.description}</p>
            <p className="text-xs text-muted-foreground">
              {row.original.item.human_id}
              {row.original.item.level ? ` · ${row.original.item.level}` : ""}
            </p>
          </div>
        ),
      },
      {
        id: "quantity",
        header: "Qty",
        cell: ({ row }) => (
          <span className="tabular-nums">
            {Number(row.original.item.net_quantity).toLocaleString(undefined, {
              maximumFractionDigits: 3,
            })}{" "}
            {row.original.item.unit}
          </span>
        ),
      },
      {
        id: "status",
        header: "Status",
        cell: ({ row }) => {
          const status = statusOf(row.original);
          return (
            <span className="inline-flex items-center gap-1">
              <span
                aria-hidden
                className="inline-block h-2 w-2 rounded-full"
                style={{ backgroundColor: STATUS_COLOURS[status] ?? "#6b7280" }}
              />
              {status}
            </span>
          );
        },
      },
      {
        id: "risk",
        header: "Risk",
        cell: ({ row }) => <span className="tabular-nums">{row.original.risk.toFixed(1)}</span>,
      },
    ],
    [],
  );

  // TanStack Table returns fresh functions each render by design, which the React Compiler
  // cannot memoise; it skips this component, as the library documents.
  // oxlint-disable-next-line react/incompatible-library
  const table = useReactTable({
    data: rows,
    columns,
    getRowId: (row) => row.item.id,
    getCoreRowModel: getCoreRowModel(),
    state: { rowSelection: selection },
    onRowSelectionChange: (updater) =>
      onSelection(typeof updater === "function" ? updater(selection) : updater),
    enableRowSelection: true,
  });

  const scroller = useRef<HTMLDivElement>(null);
  const tableRows = table.getRowModel().rows;
  const virtual = useVirtualizer({
    count: tableRows.length,
    getScrollElement: () => scroller.current,
    estimateSize: () => 52,
    overscan: 8,
  });

  const selectedIds = Object.keys(selection).filter((id) => selection[id]);
  const waiting = rows.filter((r) => ["proposed", "edited"].includes(r.item.state));

  function selectPage() {
    const next: RowSelectionState = {};
    for (const row of waiting.slice(0, PAGE_SIZE)) next[row.item.id] = true;
    onSelection(next);
  }

  return (
    <section aria-label="Review queue" className="flex h-full min-h-0 flex-col gap-2 text-sm">
      <div className="flex flex-wrap gap-2">
        <Filter
          label="Level"
          value={filters.level}
          options={levels}
          onChange={(level) => onFilters({ ...filters, level })}
        />
        <Filter
          label="Type"
          value={filters.item_type}
          options={types}
          onChange={(item_type) => onFilters({ ...filters, item_type })}
        />
        <Filter
          label="Status"
          value={filters.status}
          options={["proposed", "verified", "rejected", "manual"]}
          onChange={(status) => onFilters({ ...filters, status })}
        />
        <label className="flex items-center gap-1">
          <input
            type="checkbox"
            checked={Boolean(filters.sheet_id)}
            onChange={(event) =>
              onFilters({ ...filters, sheet_id: event.target.checked ? "current" : undefined })
            }
          />
          This sheet only
        </label>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className="rounded border px-2 py-1" onClick={selectPage}>
          Select page ({Math.min(PAGE_SIZE, waiting.length)})
        </button>
        <button
          type="button"
          className="rounded border bg-green-600 px-2 py-1 text-white disabled:opacity-50"
          disabled={!selectedIds.length || busy}
          onClick={() => onAccept(selectedIds)}
        >
          Accept {selectedIds.length || ""}
        </button>
        <select
          aria-label="Reason to reject"
          className="rounded border bg-background px-1 py-1"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
        >
          <option value="">Reason…</option>
          {reasons.map((r) => (
            <option key={r.code} value={r.code}>
              {r.label}
            </option>
          ))}
        </select>
        <button
          type="button"
          className="rounded border px-2 py-1 text-red-700 disabled:opacity-50"
          disabled={!selectedIds.length || !reason || busy}
          onClick={() => onReject(selectedIds, reason)}
        >
          Reject {selectedIds.length || ""}
        </button>
      </div>

      <div className="grid grid-cols-[1.5rem_1fr_6rem_6rem_3.5rem] gap-2 border-b pb-1 text-xs text-muted-foreground">
        {table.getHeaderGroups()[0]!.headers.map((header) => (
          <span key={header.id}>{flexRender(header.column.columnDef.header, header.getContext())}</span>
        ))}
      </div>
      <div ref={scroller} className="min-h-0 flex-1 overflow-auto" data-testid="queue-rows">
        <div style={{ height: virtual.getTotalSize(), position: "relative" }}>
          {virtual.getVirtualItems().map((slot) => {
            const row = tableRows[slot.index]!;
            const open = row.original.item.id === openId;
            return (
              <div
                key={row.id}
                role="row"
                aria-selected={open}
                data-human-id={row.original.item.human_id}
                className={`absolute left-0 grid w-full cursor-pointer grid-cols-[1.5rem_1fr_6rem_6rem_3.5rem] items-center gap-2 border-b px-1 ${
                  open ? "bg-accent" : "hover:bg-accent/50"
                }`}
                style={{ height: slot.size, transform: `translateY(${slot.start}px)` }}
                onClick={() => onOpen(row.original)}
              >
                {row.getVisibleCells().map((cell) => (
                  <div key={cell.id} className="min-w-0">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      </div>
      <p className="text-xs text-muted-foreground">
        {rows.length} items · {waiting.length} to decide · A accept, E edit, R reject, J/K next and
        previous, Ctrl+Z undo
      </p>
    </section>
  );
}

function Filter({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string | undefined;
  options: string[];
  onChange: (value: string | undefined) => void;
}) {
  return (
    <label className="flex items-center gap-1">
      {label}
      <select
        className="rounded border bg-background px-1 py-0.5"
        value={value ?? ""}
        onChange={(event) => onChange(event.target.value || undefined)}
      >
        <option value="">All</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {option.replaceAll("_", " ")}
          </option>
        ))}
      </select>
    </label>
  );
}
