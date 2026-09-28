import { useQuery } from "@tanstack/react-query";
import { useEffect, useLayoutEffect, useMemo, useRef } from "react";

import { api } from "@/api/client";

import type { TileSource } from "./DrawingViewer";
import { type Mark, MarkIndex } from "./marks";

/** The workbench's reads: Current sheets, one sheet's tiles, and what is drawn over it. */

export interface ViewSummary {
  id: string;
  kind: string;
  extent: number[];
  scale_status: string;
  denominator: number | null;
  measurable: boolean;
}

export interface WorkbenchSheet {
  sheet_id: string;
  sheet_number: string;
  revision: string | null;
  title: string | null;
  level: string | null;
  width_mm: number | null;
  height_mm: number | null;
  views: ViewSummary[];
}

export function useWorkbenchSheets(bidId: string) {
  return useQuery({
    queryKey: ["workbench-sheets", bidId],
    queryFn: async (): Promise<WorkbenchSheet[]> => {
      const { data, error } = await api.GET("/bids/{bid_id}/qto/sheets", {
        params: { path: { bid_id: bidId } },
      });
      if (error || !data) throw new Error("Could not load the sheets");
      return data;
    },
  });
}

export function useTileSource(bidId: string, sheetId: string | null) {
  return useQuery({
    queryKey: ["sheet", bidId, sheetId],
    enabled: Boolean(sheetId),
    queryFn: async (): Promise<TileSource | null> => {
      const { data, error } = await api.GET("/bids/{bid_id}/sheets/{sheet_id}", {
        params: { path: { bid_id: bidId, sheet_id: sheetId! } },
      });
      if (error || !data) throw new Error("Could not load this sheet");
      return (data.tile_source as TileSource | null) ?? null;
    },
  });
}

export function useOverlay(bidId: string, sheetId: string | null) {
  const query = useQuery({
    queryKey: ["overlay", bidId, sheetId],
    enabled: Boolean(sheetId),
    queryFn: async (): Promise<Mark[]> => {
      const { data, error } = await api.GET("/bids/{bid_id}/qto/overlay", {
        params: { path: { bid_id: bidId }, query: { sheet_id: sheetId! } },
      });
      if (error || !data) throw new Error("Could not load the overlay");
      return data as Mark[];
    },
  });
  const index = useMemo(() => new MarkIndex(query.data ?? []), [query.data]);
  return { ...query, index };
}

/**
 * The main window and the pop-out viewer talk over a BroadcastChannel (NFR-12: two
 * monitors). The queue says what to show; the viewer says what was picked.
 */
export type WorkbenchMessage =
  | { type: "sheet"; sheetId: string }
  | { type: "focus"; sheetId: string; box: number[] }
  | { type: "selected"; ids: string[] }
  | { type: "pick"; markId: string | null; itemId: string | null; additive: boolean }
  | { type: "lasso"; markIds: string[] }
  | { type: "changed" };

export function useWorkbenchChannel(
  bidId: string,
  onMessage: (message: WorkbenchMessage) => void,
) {
  const channel = useRef<BroadcastChannel | null>(null);
  const handler = useRef(onMessage);
  useLayoutEffect(() => {
    handler.current = onMessage;
  });
  useEffect(() => {
    if (typeof BroadcastChannel === "undefined") return;
    const opened = new BroadcastChannel(`firebid-workbench-${bidId}`);
    opened.onmessage = (event: MessageEvent<WorkbenchMessage>) => handler.current(event.data);
    channel.current = opened;
    return () => {
      opened.close();
      channel.current = null;
    };
  }, [bidId]);
  return (message: WorkbenchMessage) => channel.current?.postMessage(message);
}
