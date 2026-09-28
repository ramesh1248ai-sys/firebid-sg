import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";

import { api, apiErrorMessage } from "@/api/client";
import type { components } from "@/api/schema";

/** The review queue, the reasons, coverage, and the actions a person takes (FR-REV-02/03/04). */

export type QueueRow = components["schemas"]["QueueRowOut"];
export type Item = components["schemas"]["ItemOut"];
export type ReviewAction = components["schemas"]["ActionOut"];
export type Coverage = components["schemas"]["CoverageOut"];
export type Blockers = components["schemas"]["BlockersOut"];
export type Reason = components["schemas"]["ReasonOut"];
export type Group = components["schemas"]["GroupOut"];

export interface QueueFilters {
  sheet_id?: string;
  level?: string;
  item_type?: string;
  status?: string;
  system?: string;
}

function clean(filters: QueueFilters): QueueFilters {
  return Object.fromEntries(Object.entries(filters).filter(([, v]) => v)) as QueueFilters;
}

export function useQueue(bidId: string, filters: QueueFilters) {
  return useQuery({
    queryKey: ["queue", bidId, filters],
    queryFn: async (): Promise<QueueRow[]> => {
      const { data, error } = await api.GET("/bids/{bid_id}/review/queue", {
        params: { path: { bid_id: bidId }, query: clean(filters) },
      });
      if (error || !data) throw new Error("Could not load the review queue");
      return data;
    },
  });
}

export function useReasons(bidId: string) {
  return useQuery({
    queryKey: ["reasons", bidId],
    staleTime: Infinity,
    queryFn: async (): Promise<Reason[]> => {
      const { data } = await api.GET("/bids/{bid_id}/review/reasons", {
        params: { path: { bid_id: bidId } },
      });
      return data ?? [];
    },
  });
}

export function useCoverage(bidId: string) {
  return useQuery({
    queryKey: ["coverage", bidId],
    // Background jobs (detection, recompute) move it: look again until the policy is met.
    refetchInterval: (query) => (query.state.data?.met ? false : 5000),
    queryFn: async (): Promise<Coverage> => {
      const { data, error } = await api.GET("/bids/{bid_id}/review/coverage", {
        params: { path: { bid_id: bidId } },
      });
      if (error || !data) throw new Error("Could not load coverage");
      return data;
    },
  });
}

export function useBlockers(bidId: string) {
  return useQuery({
    queryKey: ["g1", bidId],
    refetchInterval: (query) => (query.state.data?.clear ? false : 5000),
    queryFn: async (): Promise<Blockers> => {
      const { data, error } = await api.GET("/bids/{bid_id}/qto/g1", {
        params: { path: { bid_id: bidId } },
      });
      if (error || !data) throw new Error("Could not check G1");
      return data;
    },
  });
}

export function useRecentActions(bidId: string) {
  return useQuery({
    queryKey: ["actions", bidId],
    queryFn: async (): Promise<ReviewAction[]> => {
      const { data } = await api.GET("/bids/{bid_id}/review/actions", {
        params: { path: { bid_id: bidId }, query: { limit: 20 } },
      });
      return data ?? [];
    },
  });
}

export function useEvidence(bidId: string, itemId: string | null) {
  return useQuery({
    queryKey: ["evidence", bidId, itemId],
    enabled: Boolean(itemId),
    queryFn: async (): Promise<Record<string, unknown>> => {
      const { data, error } = await api.GET("/bids/{bid_id}/qto/items/{item_id}/evidence", {
        params: { path: { bid_id: bidId, item_id: itemId! } },
      });
      if (error || !data) throw new Error("Could not load the evidence");
      return data as Record<string, unknown>;
    },
  });
}

/** Everything an action can change: refetch it all. */
export function useInvalidateReview(bidId: string) {
  const client = useQueryClient();
  return () =>
    Promise.all(
      ["queue", "coverage", "g1", "actions", "overlay", "evidence", "duplicates"].map((key) =>
        client.invalidateQueries({ queryKey: [key, bidId] }),
      ),
    );
}

async function unwrap<T>(
  call: Promise<{ data?: T; error?: unknown; response: Response }>,
  fallback: string,
): Promise<T> {
  const { data, error } = await call;
  if (error || data === undefined) throw new Error(apiErrorMessage(error, fallback));
  return data;
}

export interface EditInput {
  reason_code: string;
  note?: string | null;
  quantity?: string | null;
  attributes?: Record<string, string> | null;
  measure?: { view_id: string; points: number[][] } | null;
}

export type ManualInput = components["schemas"]["ManualIn"];

const WAITING = ["proposed", "edited"];
const DECIDABLE = ["proposed", "edited", "verified"];

export function useReviewActions(bidId: string, onDone?: (action: ReviewAction) => void) {
  const invalidate = useInvalidateReview(bidId);
  const client = useQueryClient();
  const path = { bid_id: bidId };

  /**
   * Show the decision in the queue at once, before the server has answered (action feedback
   * under 200 ms, NFR-12). The server's answer then refreshes everything, and a refusal
   * puts the queue back as the server has it.
   */
  const optimistic = (itemIds: string[], state: string, from: string[]) => {
    const ids = new Set(itemIds);
    void client.cancelQueries({ queryKey: ["queue", bidId] });
    client.setQueriesData<QueueRow[]>({ queryKey: ["queue", bidId] }, (rows) =>
      rows?.map((row) =>
        ids.has(row.item.id) && from.includes(row.item.state)
          ? { ...row, item: { ...row.item, state } }
          : row,
      ),
    );
  };
  const restore = () => void invalidate();
  // The latest action, answered or still on its way: what Ctrl+Z undoes, even when it is
  // pressed before the server has replied to the action it undoes.
  const latest = useRef<Promise<ReviewAction> | null>(null);
  const tracked = (promise: Promise<ReviewAction>) => {
    latest.current = promise;
    return promise;
  };
  // Feedback as soon as the server has answered; the queue, overlay and coverage refresh
  // behind it (action feedback under 200 ms, NFR-12).
  const finish = (action: ReviewAction) => {
    void invalidate();
    onDone?.(action);
    return action;
  };
  const undo = useMutation({
    mutationFn: (actionId: string) =>
      unwrap(
        api.POST("/bids/{bid_id}/review/actions/{action_id}/undo", {
          params: { path: { bid_id: bidId, action_id: actionId } },
        }),
        "Could not undo",
      ),
    onSuccess: finish,
  });
  return {
    /** Undo the latest action taken here, once it has been answered. */
    undoLast: async (): Promise<ReviewAction | null> => {
      const pending = latest.current;
      if (!pending) return null;
      latest.current = null;
      const action = await pending;
      return undo.mutateAsync(action.id);
    },
    accept: useMutation({
      mutationFn: (itemIds: string[]) =>
        tracked(
          unwrap(
            api.POST("/bids/{bid_id}/review/accept", {
              params: { path },
              body: { item_ids: itemIds },
            }),
            "Could not accept",
          ),
        ),
      onMutate: (itemIds) => optimistic(itemIds, "verified", WAITING),
      onError: restore,
      onSuccess: finish,
    }),
    reject: useMutation({
      mutationFn: (input: { itemIds: string[]; reason: string; note?: string }) =>
        tracked(
          unwrap(
            api.POST("/bids/{bid_id}/review/reject", {
              params: { path },
              body: { item_ids: input.itemIds, reason_code: input.reason, note: input.note ?? null },
            }),
            "Could not reject",
          ),
        ),
      onMutate: (input) => optimistic(input.itemIds, "rejected", DECIDABLE),
      onError: restore,
      onSuccess: finish,
    }),
    edit: useMutation({
      mutationFn: (input: { itemId: string; change: EditInput }) =>
        tracked(
          unwrap(
            api.POST("/bids/{bid_id}/review/items/{item_id}/edit", {
              params: { path: { bid_id: bidId, item_id: input.itemId } },
              body: input.change,
            }),
            "Could not save the edit",
          ),
        ),
      onSuccess: finish,
    }),
    rejectDetections: useMutation({
      mutationFn: (input: { detectionIds: string[]; reason: string; note?: string }) =>
        tracked(
          unwrap(
            api.POST("/bids/{bid_id}/review/detections/reject", {
              params: { path },
              body: {
                detection_ids: input.detectionIds,
                reason_code: input.reason,
                note: input.note ?? null,
              },
            }),
            "Could not reject the detections",
          ),
        ),
      onSuccess: finish,
    }),
    createManual: useMutation({
      mutationFn: (body: ManualInput) =>
        unwrap(
          api.POST("/bids/{bid_id}/qto/items", { params: { path }, body }),
          "Could not add the item",
        ),
      onSuccess: () => {
        void invalidate();
      },
    }),
    undo,
  };
}

// --- Duplicates, mappings, scale and G1 (P1-08 D) -------------------------------------------

export function useDuplicates(bidId: string) {
  return useQuery({
    queryKey: ["duplicates", bidId],
    queryFn: async (): Promise<Group[]> => {
      const { data, error } = await api.GET("/bids/{bid_id}/qto/duplicates", {
        params: { path: { bid_id: bidId } },
      });
      if (error || !data) throw new Error("Could not load the duplicate groups");
      return data;
    },
  });
}

export type Approval = components["schemas"]["ApprovalOut"];

export function useGateActions(bidId: string) {
  const invalidate = useInvalidateReview(bidId);
  const client = useQueryClient();
  const path = { bid_id: bidId };
  const done = () => {
    void invalidate();
    void client.invalidateQueries({ queryKey: ["workbench-sheets", bidId] });
  };
  return {
    decide: useMutation({
      mutationFn: (input: { groupId: string; decision: "confirmed" | "not_duplicate"; note?: string }) =>
        unwrap(
          api.POST("/bids/{bid_id}/qto/duplicates/{group_id}", {
            params: { path: { bid_id: bidId, group_id: input.groupId } },
            body: { decision: input.decision, note: input.note ?? null },
          }),
          "Could not record the decision",
        ),
      onSuccess: done,
    }),
    nameSymbol: useMutation({
      mutationFn: (input: { symbolKey: string; lineageId: string | null; objectType: string }) =>
        input.lineageId
          ? unwrap(
              api.POST("/bids/{bid_id}/symbols/mappings/{lineage_id}/confirm", {
                params: { path: { bid_id: bidId, lineage_id: input.lineageId } },
                body: { object_type: input.objectType },
              }),
              "Could not confirm the mapping",
            )
          : unwrap(
              api.POST("/bids/{bid_id}/symbols/unlisted", {
                params: { path },
                body: { symbol_key: input.symbolKey, object_type: input.objectType },
              }),
              "Could not name the symbol",
            ),
      onSuccess: done,
    }),
    calibrate: useMutation({
      mutationFn: (input: { sheetId: string; viewId: string; points: number[][]; distanceMm: number }) =>
        unwrap(
          api.POST("/bids/{bid_id}/sheets/{sheet_id}/views/{view_id}/calibrate", {
            params: { path: { bid_id: bidId, sheet_id: input.sheetId, view_id: input.viewId } },
            body: { points: input.points, distance_mm: input.distanceMm },
          }),
          "Could not calibrate the view",
        ),
      onSuccess: done,
    }),
    approve: useMutation({
      mutationFn: (comment: string) =>
        unwrap(
          api.POST("/bids/{bid_id}/qto/g1/approve", {
            params: { path },
            body: { comment: comment || null },
          }),
          "G1 was not approved",
        ),
      onSuccess: done,
    }),
  };
}
