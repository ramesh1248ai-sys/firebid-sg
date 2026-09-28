import { useQuery } from "@tanstack/react-query";

import { api } from "@/api/client";
import type { components } from "@/api/schema";

import type { ViewSummary, WorkbenchSheet } from "./data";

/** The manual tools' data: library types, and whether a sheet can be measured at all. */

export type ObjectType = components["schemas"]["ObjectTypeChoice"];

export function useObjectTypes(bidId: string) {
  return useQuery({
    queryKey: ["object-types", bidId],
    staleTime: Infinity,
    queryFn: async (): Promise<ObjectType[]> => {
      const { data } = await api.GET("/bids/{bid_id}/symbols/object-types", {
        params: { path: { bid_id: bidId } },
      });
      return data ?? [];
    },
  });
}

export interface ManualDraft {
  kind: "count" | "length";
  type: ObjectType;
  attributes: Record<string, string>;
  description: string;
}

export function measurableViews(sheet: WorkbenchSheet | null): ViewSummary[] {
  return sheet?.views.filter((v) => v.measurable) ?? [];
}

/** Why the tools are off on this sheet, or null when they may be used. */
export function toolsDisabledReason(sheet: WorkbenchSheet | null): string | null {
  if (!sheet) return "Choose a sheet first.";
  if (measurableViews(sheet).length === 0) {
    const statuses = [...new Set(sheet.views.map((v) => v.scale_status))].join(", ") || "none";
    return (
      `No view on ${sheet.sheet_number} has a verified or calibrated scale (${statuses}). ` +
      "Calibrate a view before measuring or placing items on it."
    );
  }
  return null;
}
