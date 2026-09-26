import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router";

import { api } from "@/api/client";
import { accessToken } from "@/auth/oidc";

/**
 * Opening a sheet (ADR-005, the tracer bullet for P1-08).
 *
 * OpenSeadragon over a custom tile source. The low levels were rendered at ingest so the
 * sheet appears at once; zooming past them asks the server for tiles it renders on the spot,
 * which is why a close-up can take a moment the first time and never again.
 *
 * **Tiles are fetched with XHR, not `<img src>`.** They are behind the API's authorisation,
 * and an image element cannot carry a bearer token — `loadTilesWithAjax` is what lets the
 * viewer send one.
 */

type TileSource = {
  width: number;
  height: number;
  tileSize: number;
  tileOverlap: number;
  minLevel: number;
  maxLevel: number;
  preRenderedLevels: number[];
  tileUrl: string;
};

type SheetDetail = {
  id: string;
  layout_name: string | null;
  width_mm: number | null;
  height_mm: number | null;
  content_class: string | null;
  tile_source: TileSource | null;
  source_ref: Record<string, unknown> | null;
};

function useSheet(bidId: string, sheetId: string) {
  return useQuery({
    queryKey: ["sheet", bidId, sheetId],
    queryFn: async (): Promise<SheetDetail> => {
      const { data, error, response } = await api.GET("/bids/{bid_id}/sheets/{sheet_id}", {
        params: { path: { bid_id: bidId, sheet_id: sheetId } },
      });
      if (response.status === 404) throw new Error("not-found");
      if (error || !data) throw new Error("Could not load this sheet");
      return data as SheetDetail;
    },
    retry: false,
  });
}

function Viewer({ source }: { source: TileSource }) {
  const container = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let viewer: { destroy: () => void } | null = null;
    let cancelled = false;

    async function open() {
      const [{ default: OpenSeadragon }, token] = await Promise.all([
        import("openseadragon"),
        accessToken(),
      ]);
      if (cancelled || !container.current) return;

      const opened = OpenSeadragon({
        element: container.current,
        prefixUrl: "https://cdn.jsdelivr.net/npm/openseadragon@5/build/openseadragon/images/",
        showNavigator: true,
        navigatorPosition: "BOTTOM_RIGHT",
        // The API authorises every tile, so they cannot be plain image requests.
        loadTilesWithAjax: true,
        ajaxHeaders: token ? { Authorization: `Bearer ${token}` } : {},
        // A close-up tile is rendered on request; give it room before giving up.
        timeout: 60000,
        gestureSettingsMouse: { clickToZoom: false },
        tileSources: {
          width: source.width,
          height: source.height,
          tileSize: source.tileSize,
          tileOverlap: source.tileOverlap,
          minLevel: source.minLevel,
          maxLevel: source.maxLevel,
          getTileUrl: (level: number, x: number, y: number) =>
            `${source.tileUrl}/${level}/${x}_${y}.webp`,
        },
      });
      opened.addHandler("open-failed", () => setFailed(true));
      viewer = opened;
    }

    open().catch(() => setFailed(true));
    return () => {
      cancelled = true;
      viewer?.destroy();
    };
  }, [source]);

  if (failed) {
    return (
      <p role="alert" className="text-sm text-destructive">
        This sheet could not be opened. It may still be being read.
      </p>
    );
  }

  return (
    <div
      ref={container}
      data-testid="sheet-viewer"
      className="h-[70vh] w-full rounded-lg border bg-neutral-100 dark:bg-neutral-900"
    />
  );
}

export function SheetViewerPage() {
  const { bidId = "", sheetId = "" } = useParams();
  const sheet = useSheet(bidId, sheetId);

  if (sheet.isPending) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Loading the sheet…
      </p>
    );
  }

  if (sheet.isError) {
    const missing = sheet.error.message === "not-found";
    return (
      <section className="space-y-3">
        <h1 className="text-2xl font-semibold tracking-tight">
          {missing ? "Sheet not found" : "Could not load this sheet"}
        </h1>
        <p role="alert" className="text-sm text-muted-foreground">
          {missing
            ? "It may not exist, or you may not be on this bid's team."
            : "Please try again in a moment."}
        </p>
        <Link to={`/bids/${bidId}/documents`} className="text-sm underline">
          Back to the tender documents
        </Link>
      </section>
    );
  }

  const { data } = sheet;
  const size =
    data.width_mm && data.height_mm
      ? `${Math.round(data.width_mm)} × ${Math.round(data.height_mm)} mm`
      : "size unknown";

  return (
    <section className="space-y-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{data.layout_name ?? "Sheet"}</h1>
        <p className="text-sm text-muted-foreground">
          {size} · {data.content_class ?? "unclassified"}
        </p>
      </div>

      {data.tile_source ? (
        <Viewer source={data.tile_source} />
      ) : (
        <p role="status" className="text-sm text-muted-foreground">
          This sheet has not finished being read yet.
        </p>
      )}

      <Link to={`/bids/${bidId}/documents`} className="inline-block text-sm underline">
        Back to the tender documents
      </Link>
    </section>
  );
}
