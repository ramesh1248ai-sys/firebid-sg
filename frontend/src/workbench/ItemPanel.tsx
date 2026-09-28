import { useState } from "react";

import type { EditInput, Item, Reason } from "./review";

/**
 * One item, with its whole evidence record (FR-REV-01, NFR-10): what it is, how much, where
 * on the drawings, how it was found and calculated (with the rule, its version, inputs and
 * their sources), and how sure the platform is. The person accepts, edits or rejects it
 * here; edit and reject need a reason (FR-REV-03).
 */

type Mode = "view" | "edit" | "reject";

export function ItemPanel({
  item,
  evidence,
  reasons,
  busy,
  error,
  mode,
  onMode,
  onAccept,
  onEdit,
  onReject,
  onRemeasure,
  onZoom,
}: {
  item: Item;
  evidence: Record<string, unknown> | undefined;
  reasons: Reason[];
  busy: boolean;
  error: string | null;
  mode: Mode;
  onMode: (mode: Mode) => void;
  onAccept: () => void;
  onEdit: (change: EditInput) => void;
  onReject: (reason: string, note: string) => void;
  onRemeasure: (reason: string, note: string) => void;
  onZoom: () => void;
}) {
  const decidable = ["proposed", "edited", "verified"].includes(item.state);
  return (
    <section aria-label={`Item ${item.human_id}`} className="space-y-3 text-sm">
      <header>
        <p className="text-xs text-muted-foreground">
          {item.human_id} · version {item.version} · {item.state}
          {item.manual ? ` · manual by ${item.manual_by ?? "?"}` : ""}
        </p>
        <h2 className="font-semibold">{item.description}</h2>
        <button type="button" className="text-xs underline" onClick={onZoom}>
          Show on the drawing
        </button>
      </header>

      <dl className="grid grid-cols-[8rem_1fr] gap-x-2 gap-y-1">
        <dt className="text-muted-foreground">Net quantity</dt>
        <dd className="tabular-nums">
          {item.net_quantity} {item.unit}
        </dd>
        <dt className="text-muted-foreground">Allowance</dt>
        <dd className="tabular-nums">
          {item.allowance_percent ? `${item.allowance_percent}% = ${item.allowance_quantity}` : "none"}
          {item.allowance_percent ? ` (with it ${item.quantity_with_allowance} ${item.unit})` : ""}
        </dd>
        <dt className="text-muted-foreground">Where</dt>
        <dd>
          {item.level ?? "level unknown"}
          {item.grid_from ? ` · ${item.grid_from}` : ""}
          {item.grid_to && item.grid_to !== item.grid_from ? ` to ${item.grid_to}` : ""}
        </dd>
        <dt className="text-muted-foreground">Sheets</dt>
        <dd>
          {item.sources.map((s) => `${s.sheet_number} rev ${s.revision}`).join(", ") || "none"}
        </dd>
        <dt className="text-muted-foreground">Confidence</dt>
        <dd>{item.confidence !== null ? item.confidence.toFixed(2) : "none"}</dd>
      </dl>

      {item.rule && (
        <div className="rounded border p-2">
          <p className="font-medium">
            Rule-derived: {item.rule.rule_key} v{item.rule.rule_version} ({item.rule.rule_status})
          </p>
          <ul className="text-xs">
            {item.rule.inputs.map((input, i) => (
              <li key={i}>
                {String(input.name ?? input.run ?? "input")} = {String(input.value ?? "")}
                {input.source ? ` (${String(input.source)})` : ""}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div>
        <p className="font-medium">Attributes</p>
        <ul className="text-xs">
          {Object.entries(item.attributes).map(([name, value]) => {
            const stated = value as { value?: string; source?: string };
            return (
              <li key={name}>
                {name.replaceAll("_", " ")}: {stated.value ?? String(value)}
                {stated.source ? ` · ${stated.source}` : ""}
              </li>
            );
          })}
        </ul>
      </div>

      {evidence && (
        <details>
          <summary className="cursor-pointer font-medium">Evidence record</summary>
          <dl className="mt-1 grid grid-cols-[8rem_1fr] gap-x-2 gap-y-1 text-xs">
            {[
              ["Detection", "detection_method"],
              ["Calculation", "calculation_method"],
              ["How", "calculation_note"],
              ["Quantity note", "quantity_note"],
              ["Geometry", "geometry_reference"],
              ["Verification", "verification_status"],
              ["Verified by", "verified_by"],
            ].map(([label, key]) => (
              <Evidence key={key} label={label!} value={evidence[key!]} />
            ))}
          </dl>
        </details>
      )}

      {error && (
        <p role="alert" className="text-destructive">
          {error}
        </p>
      )}

      {mode === "view" && decidable && (
        <div className="flex gap-2">
          <button
            type="button"
            className="rounded bg-green-600 px-3 py-1 text-white disabled:opacity-50"
            disabled={busy || item.state === "verified"}
            onClick={onAccept}
          >
            Accept
          </button>
          <button type="button" className="rounded border px-3 py-1" onClick={() => onMode("edit")}>
            Edit
          </button>
          <button
            type="button"
            className="rounded border px-3 py-1 text-red-700"
            onClick={() => onMode("reject")}
          >
            Reject
          </button>
        </div>
      )}
      {mode === "edit" && (
        <EditForm
          item={item}
          reasons={reasons}
          busy={busy}
          onCancel={() => onMode("view")}
          onSave={onEdit}
          onRemeasure={onRemeasure}
        />
      )}
      {mode === "reject" && (
        <ReasonForm
          title="Reject"
          reasons={reasons}
          busy={busy}
          onCancel={() => onMode("view")}
          onSubmit={onReject}
        />
      )}
    </section>
  );
}

function Evidence({ label, value }: { label: string; value: unknown }) {
  if (value === null || value === undefined || value === "") return null;
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="break-words">{String(value)}</dd>
    </>
  );
}

export function ReasonForm({
  title,
  reasons,
  busy,
  onCancel,
  onSubmit,
}: {
  title: string;
  reasons: Reason[];
  busy: boolean;
  onCancel: () => void;
  onSubmit: (reason: string, note: string) => void;
}) {
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  return (
    <form
      className="space-y-2 rounded border p-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (reason) onSubmit(reason, note);
      }}
    >
      <p className="font-medium">{title}</p>
      <ReasonPicker reasons={reasons} value={reason} onChange={setReason} />
      <label className="block">
        Note
        <input
          className="mt-1 block w-full rounded border bg-background px-2 py-1"
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
      </label>
      <div className="flex gap-2">
        <button
          type="submit"
          className="rounded bg-red-600 px-3 py-1 text-white disabled:opacity-50"
          disabled={!reason || busy}
        >
          {title}
        </button>
        <button type="button" className="rounded border px-3 py-1" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function ReasonPicker({
  reasons,
  value,
  onChange,
}: {
  reasons: Reason[];
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <label className="block">
      Reason
      <select
        className="mt-1 block w-full rounded border bg-background px-2 py-1"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        required
      >
        <option value="">Choose a reason…</option>
        {reasons.map((r) => (
          <option key={r.code} value={r.code}>
            {r.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function EditForm({
  item,
  reasons,
  busy,
  onCancel,
  onSave,
  onRemeasure,
}: {
  item: Item;
  reasons: Reason[];
  busy: boolean;
  onCancel: () => void;
  onSave: (change: EditInput) => void;
  onRemeasure: (reason: string, note: string) => void;
}) {
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const [quantity, setQuantity] = useState(item.unit === "m" ? "" : String(Number(item.net_quantity)));
  const stated = Object.fromEntries(
    Object.entries(item.attributes).map(([k, v]) => [k, (v as { value?: string }).value ?? ""]),
  );
  const [attributes, setAttributes] = useState<Record<string, string>>(stated);
  const isLength = item.unit === "m" && !item.manual;

  function save() {
    const changed = Object.fromEntries(
      Object.entries(attributes).filter(([k, v]) => v !== stated[k]),
    );
    onSave({
      reason_code: reason,
      note: note || null,
      quantity: !isLength && quantity !== String(Number(item.net_quantity)) ? quantity : null,
      attributes: Object.keys(changed).length ? changed : null,
    });
  }

  return (
    <form
      className="space-y-2 rounded border p-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (reason) save();
      }}
    >
      <p className="font-medium">Edit {item.human_id}</p>
      {isLength ? (
        <button
          type="button"
          className="rounded border px-2 py-1 disabled:opacity-50"
          disabled={!reason}
          onClick={() => onRemeasure(reason, note)}
        >
          Re-measure on the drawing
        </button>
      ) : (
        <label className="block">
          Quantity ({item.unit})
          <input
            className="mt-1 block w-full rounded border bg-background px-2 py-1"
            inputMode="decimal"
            value={quantity}
            onChange={(event) => setQuantity(event.target.value)}
          />
        </label>
      )}
      {Object.entries(attributes).map(([name, value]) => (
        <label key={name} className="block">
          {name.replaceAll("_", " ")}
          <input
            className="mt-1 block w-full rounded border bg-background px-2 py-1"
            value={value}
            onChange={(event) => setAttributes({ ...attributes, [name]: event.target.value })}
          />
        </label>
      ))}
      <ReasonPicker reasons={reasons} value={reason} onChange={setReason} />
      <label className="block">
        Note
        <input
          className="mt-1 block w-full rounded border bg-background px-2 py-1"
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
      </label>
      <div className="flex gap-2">
        <button
          type="submit"
          className="rounded bg-blue-600 px-3 py-1 text-white disabled:opacity-50"
          disabled={!reason || busy}
        >
          Save and verify
        </button>
        <button type="button" className="rounded border px-3 py-1" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  );
}
