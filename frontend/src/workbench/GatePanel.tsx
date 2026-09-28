import { useState } from "react";

import type { WorkbenchSheet } from "./data";
import type { ObjectType } from "./manual";
import type { Approval, Blockers, Coverage } from "./review";

/**
 * Coverage and G1 (FR-REV-04): how much of the takeoff a person has verified, by items and
 * by value, and the G1 action. G1 is enabled only when nothing blocks it; otherwise every
 * blocker is listed with a link to where it is resolved.
 */

export type Tab = "queue" | "duplicates" | "setup" | "g1";

export function GatePanel({
  coverage,
  blockers,
  canApprove,
  busy,
  error,
  approval,
  onGo,
  onOpenItem,
  onApprove,
}: {
  coverage: Coverage | undefined;
  blockers: Blockers | undefined;
  canApprove: boolean;
  busy: boolean;
  error: string | null;
  approval: Approval | null;
  onGo: (tab: Tab, status?: string) => void;
  onOpenItem: (itemId: string) => void;
  onApprove: (comment: string) => void;
}) {
  const [comment, setComment] = useState("");
  const cover = blockers?.coverage as Coverage | undefined;
  const shown = coverage ?? cover;
  const clear = Boolean(blockers?.clear);
  return (
    <section aria-label="Coverage and G1" className="space-y-3 text-sm">
      {shown && (
        <div className="space-y-2">
          <Meter
            label="Items verified"
            percent={shown.items_percent}
            detail={`${shown.items_verified} of ${shown.items_total}`}
            policy={shown.policy_percent}
          />
          <Meter
            label="Value verified"
            percent={shown.value_percent}
            detail={shown.value_basis}
          />
        </div>
      )}

      {blockers && !clear && (
        <div role="list" aria-label="What blocks G1" className="space-y-1">
          <p className="font-medium">G1 is blocked by:</p>
          {!shown?.met && (
            <Blocker>
              Coverage is {shown?.items_percent ?? 0}%, the policy is {shown?.policy_percent}%.{" "}
              <button type="button" className="underline" onClick={() => onGo("queue", "proposed")}>
                Items still to decide
              </button>
            </Blocker>
          )}
          {blockers.unresolved_groups.length > 0 && (
            <Blocker>
              {blockers.unresolved_groups.length} unresolved duplicate group(s).{" "}
              <button type="button" className="underline" onClick={() => onGo("duplicates")}>
                Resolve them
              </button>
            </Blocker>
          )}
          {blockers.unmapped_symbols.length > 0 && (
            <Blocker>
              {blockers.unmapped_symbols.length} symbol type(s) on Current sheets nobody has
              named.{" "}
              <button type="button" className="underline" onClick={() => onGo("setup")}>
                Name them
              </button>
            </Blocker>
          )}
          {blockers.incomplete_items.map((item) => (
            <Blocker key={String(item.id)}>
              {String(item.human_id)} has an incomplete evidence record (
              {(item.missing as string[]).join(", ")}).{" "}
              <button
                type="button"
                className="underline"
                onClick={() => onOpenItem(String(item.id))}
              >
                Open it
              </button>
            </Blocker>
          ))}
          {blockers.pending_work.length > 0 && (
            <Blocker>
              Drawings are still being read or detected (
              {blockers.pending_work.map((w) => `${w.jobs} ${w.task}`).join(", ")}). Wait for them.
            </Blocker>
          )}
        </div>
      )}

      {approval ? (
        <p role="status" className="rounded bg-green-50 p-2 text-green-900 dark:bg-green-950 dark:text-green-100">
          G1 approved at {new Date(approval.decided_at).toLocaleString()}.
        </p>
      ) : (
        <div className="space-y-2">
          <label className="block">
            Comment
            <input
              className="mt-1 block w-full rounded border bg-background px-2 py-1"
              value={comment}
              onChange={(event) => setComment(event.target.value)}
            />
          </label>
          <button
            type="button"
            className="rounded bg-green-700 px-3 py-1 text-white disabled:opacity-50"
            disabled={!clear || !canApprove || busy}
            onClick={() => onApprove(comment)}
          >
            Approve G1: quantities verified
          </button>
          {!canApprove && (
            <p className="text-xs text-muted-foreground">Only a Senior Estimator approves G1.</p>
          )}
          {error && (
            <p role="alert" className="text-destructive">
              {error}
            </p>
          )}
        </div>
      )}
    </section>
  );
}

function Blocker({ children }: { children: React.ReactNode }) {
  return (
    <p role="listitem" className="rounded border border-amber-300 p-2 dark:border-amber-700">
      {children}
    </p>
  );
}

function Meter({
  label,
  percent,
  detail,
  policy,
}: {
  label: string;
  percent: number;
  detail: string;
  policy?: number;
}) {
  return (
    <div>
      <div className="flex justify-between">
        <span>{label}</span>
        <span className="tabular-nums">{percent.toFixed(1)}%</span>
      </div>
      <div
        className="h-2 rounded bg-muted"
        role="meter"
        aria-label={label}
        aria-valuenow={percent}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className="h-2 rounded bg-green-600"
          style={{ width: `${Math.min(100, percent)}%` }}
        />
      </div>
      <p className="text-xs text-muted-foreground">
        {detail}
        {policy !== undefined ? ` · policy ${policy}%` : ""}
      </p>
    </div>
  );
}

/** Symbols nobody has named, and views whose scale cannot be trusted (P1-03/P1-04 APIs). */
export function SetupPanel({
  blockers,
  types,
  sheet,
  busy,
  error,
  calibrating,
  onName,
  onStartCalibration,
  onCalibrate,
  onCancelCalibration,
}: {
  blockers: Blockers | undefined;
  types: ObjectType[];
  sheet: WorkbenchSheet | null;
  busy: boolean;
  error: string | null;
  calibrating: { viewId: string; points: number[][] } | null;
  onName: (symbolKey: string, lineageId: string | null, objectType: string) => void;
  onStartCalibration: (viewId: string) => void;
  onCalibrate: (distanceMm: number) => void;
  onCancelCalibration: () => void;
}) {
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [distance, setDistance] = useState("");
  const unmapped = blockers?.unmapped_symbols ?? [];
  return (
    <section aria-label="Symbols and scale" className="space-y-4 text-sm">
      {error && (
        <p role="alert" className="text-destructive">
          {error}
        </p>
      )}
      <div className="space-y-2">
        <p className="font-medium">Symbols nobody has named</p>
        {unmapped.length === 0 && (
          <p className="text-muted-foreground">Every symbol on the Current sheets is named.</p>
        )}
        <ul className="space-y-2" aria-label="Unnamed symbols">
          {unmapped.map((symbol) => {
            const key = String(symbol.symbol_key);
            return (
              <li key={key} className="rounded border p-2">
                <p>
                  {String(symbol.description ?? "Unlisted shape")} · {String(symbol.instances)} on{" "}
                  {(symbol.sheets as string[]).length} sheet(s) · {String(symbol.status)}
                </p>
                <div className="mt-1 flex gap-2">
                  <select
                    aria-label={`Type for ${key}`}
                    className="min-w-0 flex-1 rounded border bg-background px-1"
                    value={choices[key] ?? ""}
                    onChange={(event) => setChoices({ ...choices, [key]: event.target.value })}
                  >
                    <option value="">What is it?</option>
                    {types.map((t) => (
                      <option key={t.key} value={t.key}>
                        {t.label}
                      </option>
                    ))}
                  </select>
                  <button
                    type="button"
                    className="rounded border px-2 disabled:opacity-50"
                    disabled={!choices[key] || busy}
                    onClick={() =>
                      onName(
                        key,
                        (symbol.mapping_lineage_id as string | null) ?? null,
                        choices[key]!,
                      )
                    }
                  >
                    Name
                  </button>
                </div>
              </li>
            );
          })}
        </ul>
      </div>

      <div className="space-y-2">
        <p className="font-medium">Scale of {sheet?.sheet_number ?? "this sheet"}</p>
        <ul className="space-y-1">
          {(sheet?.views ?? []).map((view) => (
            <li key={view.id} className="flex items-center justify-between gap-2">
              <span>
                {view.kind} · {view.scale_status}
                {view.denominator ? ` 1:${view.denominator}` : ""}
              </span>
              {!view.measurable && !calibrating && (
                <button
                  type="button"
                  className="rounded border px-2"
                  onClick={() => onStartCalibration(view.id)}
                >
                  Calibrate
                </button>
              )}
            </li>
          ))}
        </ul>
        {calibrating && (
          <div className="space-y-2 rounded border p-2">
            <p>
              {calibrating.points.length < 2
                ? "Click two points on the drawing whose real distance you know, then press Enter."
                : "How far apart are those two points, in real millimetres?"}
            </p>
            {calibrating.points.length >= 2 && (
              <label className="block">
                Distance (mm)
                <input
                  className="mt-1 block w-full rounded border bg-background px-2 py-1"
                  inputMode="decimal"
                  value={distance}
                  onChange={(event) => setDistance(event.target.value)}
                />
              </label>
            )}
            <div className="flex gap-2">
              <button
                type="button"
                className="rounded bg-blue-600 px-2 py-1 text-white disabled:opacity-50"
                disabled={calibrating.points.length < 2 || !(Number(distance) > 0) || busy}
                onClick={() => onCalibrate(Number(distance))}
              >
                Calibrate
              </button>
              <button type="button" className="rounded border px-2 py-1" onClick={onCancelCalibration}>
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
