import { useState } from "react";

import type { WorkbenchSheet } from "./data";
import { type ManualDraft, type ObjectType, toolsDisabledReason } from "./manual";

/**
 * Manual takeoff (FR-QTO-11): what the platform missed, added by a person.
 *
 * - **Count:** click each one on the drawing, then save them as one item.
 * - **Length:** click along the pipe, double-click or press Enter to finish.
 *
 * Both work only on a view whose scale is verified or calibrated, and say so when a sheet
 * has none. The type comes from the canonical object library, with its attributes.
 */

export function ManualTools({
  sheet,
  types,
  active,
  placed,
  busy,
  error,
  onStart,
  onSave,
  onCancel,
}: {
  sheet: WorkbenchSheet | null;
  types: ObjectType[];
  active: ManualDraft | null;
  placed: number;
  busy: boolean;
  error: string | null;
  onStart: (draft: ManualDraft) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  const [kind, setKind] = useState<"count" | "length">("count");
  const [typeKey, setTypeKey] = useState("");
  const [attributes, setAttributes] = useState<Record<string, string>>({});
  const disabled = toolsDisabledReason(sheet);
  const offered = types.filter((t) => t.measure === kind);
  const type = offered.find((t) => t.key === typeKey) ?? null;
  const schema = Object.keys((type?.attribute_schema ?? {}) as Record<string, unknown>);

  if (active) {
    return (
      <section aria-label="Manual takeoff" className="space-y-2 rounded-lg border p-2 text-sm">
        <p className="font-medium">
          {active.kind === "count"
            ? `Counting ${active.type.label}: click each one on the drawing (${placed} placed).`
            : `Measuring ${active.type.label}: click along it, double-click or Enter to finish.`}
        </p>
        {error && (
          <p role="alert" className="text-destructive">
            {error}
          </p>
        )}
        <div className="flex gap-2">
          {active.kind === "count" && (
            <button
              type="button"
              className="rounded bg-blue-600 px-2 py-1 text-white disabled:opacity-50"
              disabled={!placed || busy}
              onClick={onSave}
            >
              Save {placed}
            </button>
          )}
          <button type="button" className="rounded border px-2 py-1" onClick={onCancel}>
            Stop
          </button>
        </div>
      </section>
    );
  }

  return (
    <details className="rounded-lg border p-2 text-sm">
      <summary className="cursor-pointer font-medium">Add what was missed</summary>
      <section aria-label="Manual takeoff" className="mt-2 space-y-2">
      {disabled ? (
        <p role="note" className="text-muted-foreground">
          {disabled}
        </p>
      ) : (
        <>
          <div className="flex gap-3">
            {(["count", "length"] as const).map((option) => (
              <label key={option} className="flex items-center gap-1">
                <input
                  type="radio"
                  name="manual-kind"
                  checked={kind === option}
                  onChange={() => {
                    setKind(option);
                    setTypeKey("");
                  }}
                />
                {option === "count" ? "Count" : "Length"}
              </label>
            ))}
          </div>
          <label className="block">
            Type
            <select
              className="mt-1 block w-full rounded border bg-background px-2 py-1"
              value={typeKey}
              onChange={(event) => {
                setTypeKey(event.target.value);
                setAttributes({});
              }}
            >
              <option value="">Choose from the library…</option>
              {offered.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          {schema.map((name) => (
            <label key={name} className="block">
              {name.replaceAll("_", " ")}
              <input
                className="mt-1 block w-full rounded border bg-background px-2 py-1"
                value={attributes[name] ?? ""}
                onChange={(event) => setAttributes({ ...attributes, [name]: event.target.value })}
              />
            </label>
          ))}
          <button
            type="button"
            className="rounded border px-2 py-1 disabled:opacity-50"
            disabled={!type}
            onClick={() =>
              type &&
              onStart({
                kind,
                type,
                attributes: Object.fromEntries(Object.entries(attributes).filter(([, v]) => v)),
                description: describe(type, attributes),
              })
            }
          >
            {kind === "count" ? "Start counting" : "Start measuring"}
          </button>
        </>
      )}
      </section>
    </details>
  );
}

function describe(type: ObjectType, attributes: Record<string, string>): string {
  const stated = Object.entries(attributes)
    .filter(([, v]) => v)
    .map(([k, v]) => `${k.replaceAll("_", " ")} ${v}`);
  return stated.length ? `${type.label} (${stated.join(", ")})` : type.label;
}
