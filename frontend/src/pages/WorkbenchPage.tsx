import { useCallback, useMemo, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import { DrawingViewer, type Tool } from "@/workbench/DrawingViewer";
import { useOverlay, useTileSource, useWorkbenchChannel, useWorkbenchSheets } from "@/workbench/data";
import { LayerPanel } from "@/workbench/LayerPanel";
import { type Box, type Layers, type Mark, NO_LAYERS_HIDDEN, pickInto } from "@/workbench/marks";

/**
 * The verification workbench (P1-08): the drawing with every proposal over it, the review
 * queue beside it, and the item being reviewed.
 *
 * Only Current sheets are offered (guardrail 6). The viewer can pop out into its own window
 * for a second monitor; the two stay in step over a BroadcastChannel (NFR-12).
 */
export function WorkbenchPage() {
  const { bidId = "" } = useParams();
  const [search, setSearch] = useSearchParams();
  const sheets = useWorkbenchSheets(bidId);
  const sheetId = search.get("sheet") ?? sheets.data?.[0]?.sheet_id ?? null;
  const sheet = sheets.data?.find((s) => s.sheet_id === sheetId) ?? null;
  const tiles = useTileSource(bidId, sheetId);
  const overlay = useOverlay(bidId, sheetId);
  const [layers, setLayers] = useState<Layers>(NO_LAYERS_HIDDEN);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [focus, setFocus] = useState<{ box: number[]; key: number } | null>(null);
  const [tool] = useState<Tool>("select");
  const [, setView] = useState<Box | null>(null);

  const chooseSheet = useCallback(
    (id: string) => {
      setSelected(new Set());
      setSearch((params) => {
        params.set("sheet", id);
        return params;
      });
    },
    [setSearch],
  );

  const post = useWorkbenchChannel(bidId, (message) => {
    if (message.type === "pick" && message.markId) {
      setSelected((current) => pickInto(current, message.markId!, message.additive));
    }
    if (message.type === "lasso") setSelected(new Set(message.markIds));
  });

  const onPick = useCallback(
    (mark: Mark | null, additive: boolean) => {
      setSelected((current) => (mark ? pickInto(current, mark.id, additive) : new Set()));
    },
    [],
  );

  const onLasso = useCallback((marks: Mark[]) => {
    setSelected(new Set(marks.map((m) => m.id)));
  }, []);

  const selectedMarks = useMemo(
    () => overlay.index.marks.filter((m) => selected.has(m.id)),
    [overlay.index, selected],
  );

  function popOut() {
    if (!sheetId) return;
    window.open(
      `/bids/${bidId}/workbench/viewer?sheet=${sheetId}`,
      `firebid-viewer-${bidId}`,
      "popup,width=1400,height=900",
    );
  }

  if (sheets.isPending) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Loading the workbench…
      </p>
    );
  }
  if (sheets.isError) {
    return (
      <p role="alert" className="text-sm text-destructive">
        Could not load this bid's sheets.
      </p>
    );
  }
  if (!sheets.data.length) {
    return (
      <section className="space-y-3">
        <h1 className="text-2xl font-semibold tracking-tight">Workbench</h1>
        <p className="text-sm text-muted-foreground">
          No Current sheets yet. Upload drawings and register their revisions first.
        </p>
        <Link to={`/bids/${bidId}/documents`} className="text-sm underline">
          Tender documents
        </Link>
      </section>
    );
  }

  return (
    <section className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">Workbench</h1>
        <label className="flex items-center gap-2 text-sm">
          Sheet
          <select
            className="rounded-md border bg-background px-2 py-1"
            value={sheetId ?? ""}
            onChange={(event) => chooseSheet(event.target.value)}
          >
            {sheets.data.map((s) => (
              <option key={s.sheet_id} value={s.sheet_id}>
                {s.sheet_number} {s.revision ? `rev ${s.revision}` : ""}
                {s.level ? ` · ${s.level}` : ""}
              </option>
            ))}
          </select>
        </label>
        <button type="button" className="text-sm underline" onClick={popOut}>
          Pop out the drawing
        </button>
      </div>

      <div className="grid gap-3 lg:grid-cols-[13rem_1fr_26rem]">
        <aside className="rounded-lg border p-3">
          <LayerPanel marks={overlay.index.marks} layers={layers} onChange={setLayers} />
        </aside>

        <div>
          {tiles.data && sheet?.width_mm ? (
            <DrawingViewer
              source={tiles.data}
              widthMm={sheet.width_mm}
              index={overlay.index}
              layers={layers}
              selected={selected}
              focus={focus}
              tool={tool}
              onPick={onPick}
              onLasso={onLasso}
              onViewChange={setView}
            />
          ) : (
            <p role="status" className="text-sm text-muted-foreground">
              {tiles.isPending ? "Opening the sheet…" : "This sheet has not finished being read."}
            </p>
          )}
        </div>

        <aside className="space-y-2 rounded-lg border p-3 text-sm" aria-label="Selection">
          <p className="font-medium">
            {selectedMarks.length
              ? `${selectedMarks.length} selected`
              : "Click a mark, or Shift-drag to lasso"}
          </p>
          <ul className="space-y-1">
            {selectedMarks.slice(0, 50).map((mark) => (
              <li key={mark.id}>
                <button
                  type="button"
                  className="underline"
                  onClick={() => {
                    setFocus({ box: mark.box, key: Date.now() });
                    if (sheetId) post({ type: "focus", sheetId, box: mark.box });
                  }}
                >
                  {mark.item_human_id ?? "not taken off"}
                </button>{" "}
                {mark.object_type.replaceAll("_", " ")} · {mark.status}
              </li>
            ))}
          </ul>
        </aside>
      </div>
    </section>
  );
}

/** The drawing alone, in its own window for a second monitor (NFR-12). */
export function PopoutViewerPage() {
  const { bidId = "" } = useParams();
  const [search] = useSearchParams();
  const [sheetId, setSheetId] = useState<string | null>(search.get("sheet"));
  const sheets = useWorkbenchSheets(bidId);
  const sheet = sheets.data?.find((s) => s.sheet_id === sheetId) ?? null;
  const tiles = useTileSource(bidId, sheetId);
  const overlay = useOverlay(bidId, sheetId);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [focus, setFocus] = useState<{ box: number[]; key: number } | null>(null);

  const post = useWorkbenchChannel(bidId, (message) => {
    if (message.type === "sheet") setSheetId(message.sheetId);
    if (message.type === "focus") {
      setSheetId(message.sheetId);
      setFocus({ box: message.box, key: Date.now() });
    }
    if (message.type === "selected") setSelected(new Set(message.ids));
    if (message.type === "changed") void overlay.refetch();
  });

  return (
    <section className="h-screen p-2">
      {tiles.data && sheet?.width_mm ? (
        <DrawingViewer
          source={tiles.data}
          widthMm={sheet.width_mm}
          index={overlay.index}
          layers={NO_LAYERS_HIDDEN}
          selected={selected}
          focus={focus}
          className="h-full w-full"
          onPick={(mark, additive) =>
            post({
              type: "pick",
              markId: mark?.id ?? null,
              itemId: mark?.item_id ?? null,
              additive,
            })
          }
          onLasso={(marks) => post({ type: "lasso", markIds: marks.map((m) => m.id) })}
        />
      ) : (
        <p role="status" className="text-sm text-muted-foreground">
          Opening the sheet…
        </p>
      )}
    </section>
  );
}
