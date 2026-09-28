import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

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

export function useReviewActions(bidId: string, onDone?: (action: ReviewAction) => void) {
  const invalidate = useInvalidateReview(bidId);
  const path = { bid_id: bidId };
  const finish = async (action: ReviewAction) => {
    await invalidate();
    onDone?.(action);
    return action;
  };
  return {
    accept: useMutation({
      mutationFn: (itemIds: string[]) =>
        unwrap(
          api.POST("/bids/{bid_id}/review/accept", {
            params: { path },
            body: { item_ids: itemIds },
          }),
          "Could not accept",
        ),
      onSuccess: finish,
    }),
    reject: useMutation({
      mutationFn: (input: { itemIds: string[]; reason: string; note?: string }) =>
        unwrap(
          api.POST("/bids/{bid_id}/review/reject", {
            params: { path },
            body: { item_ids: input.itemIds, reason_code: input.reason, note: input.note ?? null },
          }),
          "Could not reject",
        ),
      onSuccess: finish,
    }),
    edit: useMutation({
      mutationFn: (input: { itemId: string; change: EditInput }) =>
        unwrap(
          api.POST("/bids/{bid_id}/review/items/{item_id}/edit", {
            params: { path: { bid_id: bidId, item_id: input.itemId } },
            body: input.change,
          }),
          "Could not save the edit",
        ),
      onSuccess: finish,
    }),
    rejectDetections: useMutation({
      mutationFn: (input: { detectionIds: string[]; reason: string; note?: string }) =>
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
      onSuccess: finish,
    }),
    undo: useMutation({
      mutationFn: (actionId: string) =>
        unwrap(
          api.POST("/bids/{bid_id}/review/actions/{action_id}/undo", {
            params: { path: { bid_id: bidId, action_id: actionId } },
          }),
          "Could not undo",
        ),
      onSuccess: finish,
    }),
  };
}
