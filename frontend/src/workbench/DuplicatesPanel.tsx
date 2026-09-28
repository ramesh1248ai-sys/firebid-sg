import { useMemo, useState } from "react";

import { DrawingViewer } from "./DrawingViewer";
import { useOverlay, useTileSource, type WorkbenchSheet } from "./data";
import { boundsOf, NO_LAYERS_HIDDEN } from "./marks";
import type { Group } from "./review";

/**
 * Suspected duplicates, side by side (FR-QTO-08): each sheet of the group in its own small
 * viewer, zoomed to what it repeats, with the repeated marks outlined. The person keeps one
 * of each (the general plan's, by default) or says they are not duplicates.
 */

const KINDS: Record<string, string> = {
  enlarged_plan: "Enlarged plan repeats the general plan",
  match_line: "Plans overlap across a match line",
  schematic: "Schematic or section repeats plan items",
};

export function DuplicatesPanel({
  bidId,
  groups,
  sheets,
  busy,
  error,
  onDecide,
}: {
  bidId: string;
  groups: Group[];
  sheets: WorkbenchSheet[];
  busy: boolean;
  error: string | null;
  onDecide: (groupId: string, decision: "confirmed" | "not_duplicate") => void;
}) {
  const [openId, setOpenId] = useState<string | null>(null);
  if (!groups.length) {
    return <p className="text-sm text-muted-foreground">No repeats found across these sheets.</p>;
  }
  return (
    <section aria-label="Duplicate groups" className="space-y-2 text-sm">
      {error && (
        <p role="alert" className="text-destructive">
          {error}
        </p>
      )}
      <ul className="space-y-2">
        {groups.map((group) => {
          const bySheet = new Map<string, Group["members"]>();
          for (const member of group.members) {
            const key = String(member.sheet_id);
            bySheet.set(key, [...(bySheet.get(key) ?? []), member]);
          }
          const open = openId === group.id;
          return (
            <li key={group.id} className="rounded border p-2" aria-label={`Group ${group.kind}`}>
              <button
                type="button"
                className="w-full text-left"
                onClick={() => setOpenId(open ? null : group.id)}
              >
                <span
                  className={`mr-2 rounded px-1 text-xs ${
                    group.status === "unresolved" ? "bg-amber-200 text-amber-900" : "bg-muted"
                  }`}
                >
                  {group.status.replaceAll("_", " ")}
                </span>
                {KINDS[group.kind] ?? group.kind}
                {group.level ? ` · ${group.level}` : ""} · {group.members.length} marks on{" "}
                {bySheet.size} sheet{bySheet.size === 1 ? "" : "s"}
              </button>
              <p className="text-xs text-muted-foreground">{group.reason}</p>
              {group.decided_by && (
                <p className="text-xs">
                  Decided by {group.decided_by}
                  {group.decision_note ? `: ${group.decision_note}` : ""}
                </p>
              )}
              {open && (
                <div className="mt-2 space-y-2">
                  <div className="grid grid-cols-2 gap-2">
                    {[...bySheet.entries()].map(([sheetId, members]) => (
                      <MiniViewer
                        key={sheetId}
                        bidId={bidId}
                        sheet={sheets.find((s) => s.sheet_id === sheetId) ?? null}
                        memberIds={members.map((m) => String(m.id))}
                        kept={members.filter((m) => m.keep).length}
                        total={members.length}
                      />
                    ))}
                  </div>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      className="rounded bg-blue-600 px-2 py-1 text-white disabled:opacity-50"
                      disabled={busy || group.status === "confirmed"}
                      onClick={() => onDecide(group.id, "confirmed")}
                    >
                      Keep one of each
                    </button>
                    <button
                      type="button"
                      className="rounded border px-2 py-1 disabled:opacity-50"
                      disabled={busy || group.status === "not_duplicate"}
                      onClick={() => onDecide(group.id, "not_duplicate")}
                    >
                      Not duplicates: count both
                    </button>
                  </div>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function MiniViewer({
  bidId,
  sheet,
  memberIds,
  kept,
  total,
}: {
  bidId: string;
  sheet: WorkbenchSheet | null;
  memberIds: string[];
  kept: number;
  total: number;
}) {
  const tiles = useTileSource(bidId, sheet?.sheet_id ?? null);
  const overlay = useOverlay(bidId, sheet?.sheet_id ?? null);
  const selected = useMemo(() => new Set(memberIds), [memberIds]);
  const focus = useMemo(() => {
    const marks = overlay.index.marks.filter((m) => selected.has(m.id));
    if (!marks.length) return null;
    const corners = marks.flatMap((m) => [
      [m.box[0]!, m.box[1]!],
      [m.box[2]!, m.box[3]!],
    ]);
    return { box: [...boundsOf(corners)], key: marks.length };
  }, [overlay.index, selected]);
  return (
    <figure className="space-y-1">
      <figcaption className="text-xs">
        {sheet?.sheet_number ?? "sheet"} · {kept ? `counted here (${total})` : `not counted (${total})`}
      </figcaption>
      {tiles.data && sheet?.width_mm ? (
        <DrawingViewer
          source={tiles.data}
          widthMm={sheet.width_mm}
          index={overlay.index}
          layers={NO_LAYERS_HIDDEN}
          selected={selected}
          focus={focus}
          className="h-56 w-full"
        />
      ) : (
        <p className="text-xs text-muted-foreground">Opening…</p>
      )}
    </figure>
  );
}
