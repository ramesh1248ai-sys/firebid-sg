import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";

import type { Mark } from "./marks";

/**
 * What a revision changed (FR-DOC-08, FR-QTO-12): the sheet's changes from the revision it
 * superseded, and the takeoff's changes from its baseline. The reads, and the marks the
 * viewer draws for a comparison.
 */

export interface Change {
  change: "added" | "removed" | "changed" | string;
  kind: string;
  object_type: string;
  x: number;
  y: number;
  old_id: string | null;
  new_id: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  points: number[][];
}

export interface RevisionDiff {
  old: { revision?: string | null; sheet_number?: string | null };
  new: { revision?: string | null; sheet_number?: string | null };
  alignment: { method?: string; matched?: number };
  counts: Record<string, number>;
  changes: Change[];
}

export interface ItemDelta {
  change: string;
  human_id: string;
  description: string;
  unit: string;
  before: string | null;
  after: string | null;
  difference: string;
  state_after: string | null;
  to_review: boolean;
}

export interface LineDelta {
  line: string;
  unit: string;
  before: string;
  after: string;
  difference: string;
}

export interface Delta {
  baseline: { reason: string; taken_at: string };
  counts: Record<string, number>;
  items: ItemDelta[];
  lines: LineDelta[];
  gate: { status?: string; to_verify?: string[]; reason?: string };
}

export function useRevisionDiff(bidId: string, sheetId: string | null) {
  return useQuery({
    queryKey: ["revision-diff", bidId, sheetId],
    enabled: Boolean(sheetId),
    retry: false,
    queryFn: async (): Promise<RevisionDiff | null> => {
      const { data, error, response } = await api.GET(
        "/bids/{bid_id}/sheets/{sheet_id}/revision-diff",
        { params: { path: { bid_id: bidId, sheet_id: sheetId! } } },
      );
      // A first issue superseded nothing: there is no comparison, which is not a failure.
      if (response.status === 404) return null;
      if (error || !data)
        throw new Error("Could not compare this sheet's revisions");
      const found = data as unknown as RevisionDiff;
      return Array.isArray(found.changes) ? found : null;
    },
  });
}

export function useDelta(bidId: string) {
  return useQuery({
    queryKey: ["qto-delta", bidId],
    retry: false,
    queryFn: async (): Promise<Delta | null> => {
      const { data, error, response } = await api.GET(
        "/bids/{bid_id}/qto/delta",
        {
          params: { path: { bid_id: bidId } },
        },
      );
      if (response.status === 404) return null;
      if (error || !data) throw new Error("Could not load the delta report");
      const found = data as unknown as Delta;
      return Array.isArray(found.items) ? found : null;
    },
  });
}

const HALF = 2.5; // a change with no line of its own is marked 5 mm square on paper

/** The changes as marks the viewer draws: on the newer sheet, coloured by what changed. */
export function changeMarks(changes: Change[]): Mark[] {
  return changes.map((change, index) => ({
    id: `change:${index}`,
    kind: change.points.length >= 2 ? "run" : "detection",
    object_type: change.object_type,
    box:
      change.points.length >= 2
        ? [
            Math.min(...change.points.map((p) => p[0]!)),
            Math.min(...change.points.map((p) => p[1]!)),
            Math.max(...change.points.map((p) => p[0]!)),
            Math.max(...change.points.map((p) => p[1]!)),
          ]
        : [change.x - HALF, change.y - HALF, change.x + HALF, change.y + HALF],
    status: change.change,
    band: "low",
    confidence: null,
    item_id: null,
    item_human_id: null,
    x: change.x,
    y: change.y,
    points: change.points,
    label: describe(change),
  }));
}

function words(value: string): string {
  return value.replace(/_/g, " ");
}

function stated(values: Record<string, unknown> | null): string {
  return Object.entries(values ?? {})
    .map(([key, value]) => `${words(key)} ${words(String(value))}`)
    .join(", ");
}

export function describe(change: Change): string {
  const what = words(change.object_type);
  if (change.change === "changed")
    return `${what}: ${stated(change.before)} → ${stated(change.after)}`;
  return what;
}
