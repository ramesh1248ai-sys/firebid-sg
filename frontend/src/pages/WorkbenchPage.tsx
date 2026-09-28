import type { RowSelectionState } from "@tanstack/react-table";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router";

import { useAuth } from "@/auth/session";
import { DrawingViewer, type Tool } from "@/workbench/DrawingViewer";
import { DuplicatesPanel } from "@/workbench/DuplicatesPanel";
import { GatePanel, SetupPanel, type Tab } from "@/workbench/GatePanel";
import {
  useOverlay,
  useTileSource,
  useWorkbenchChannel,
  useWorkbenchSheets,
  type WorkbenchSheet,
} from "@/workbench/data";
import { ItemPanel, ReasonForm } from "@/workbench/ItemPanel";
import { LayerPanel } from "@/workbench/LayerPanel";
import { type ManualDraft, useObjectTypes } from "@/workbench/manual";
import { ManualTools } from "@/workbench/ManualTools";
import { type Layers, type Mark, NO_LAYERS_HIDDEN, pickInto } from "@/workbench/marks";
import { QueuePanel } from "@/workbench/QueuePanel";
import {
  type EditInput,
  type QueueFilters,
  type Approval,
  type QueueRow,
  useBlockers,
  useCoverage,
  useDuplicates,
  useEvidence,
  useGateActions,
  useQueue,
  useReasons,
  useRecentActions,
  useReviewActions,
} from "@/workbench/review";

/**
 * The verification workbench (P1-08): the drawing with every proposal over it, the review
 * queue beside it, and the item being reviewed with its evidence.
 *
 * Only Current sheets are offered (guardrail 6). Clicking a queue row zooms the drawing to
 * the item's evidence and opens it (NFR-10); clicking a mark opens the item it counts
 * towards. The viewer can pop out into its own window for a second monitor (NFR-12).
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
  const [lassoed, setLassoed] = useState<Set<string>>(new Set());
  const [focus, setFocus] = useState<{ box: number[]; key: number } | null>(null);
  const [tool, setTool] = useState<Tool>("select");
  const [filters, setFilters] = useState<QueueFilters>({});
  const [selection, setSelection] = useState<RowSelectionState>({});
  const [openId, setOpenId] = useState<string | null>(search.get("item"));
  const [mode, setMode] = useState<"view" | "edit" | "reject">("view");
  const [remeasure, setRemeasure] = useState<{ reason: string; note: string } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("queue");
  const [calibrating, setCalibrating] = useState<{ viewId: string; points: number[][] } | null>(
    null,
  );
  const [approval, setApproval] = useState<Approval | null>(null);
  const duplicates = useDuplicates(bidId);
  const coverage = useCoverage(bidId);
  const blockers = useBlockers(bidId);
  const gate = useGateActions(bidId);
  const { session } = useAuth();
  const canApprove = Boolean(session?.roles.includes("senior_estimator"));
  const [manual, setManual] = useState<ManualDraft | null>(null);
  const [placed, setPlaced] = useState<number[][]>([]);
  const objectTypes = useObjectTypes(bidId);

  const queue = useQueue(bidId, {
    ...filters,
    sheet_id: filters.sheet_id ? (sheetId ?? undefined) : undefined,
  });
  const reasons = useReasons(bidId);
  const recent = useRecentActions(bidId);
  const evidence = useEvidence(bidId, openId);
  const rows = useMemo(() => queue.data ?? [], [queue.data]);
  const open = rows.find((r) => r.item.id === openId) ?? null;

  // The pop-out's picks open items here; `openItem` is defined below, so reach it by ref.
  const opener = useRef<(itemId: string) => void>(() => {});
  const post = useWorkbenchChannel(bidId, (message) => {
    if (message.type === "pick" && message.itemId) opener.current(message.itemId);
    if (message.type === "lasso") setLassoed(new Set(message.markIds));
  });

  const openRef = useRef(openId);
  useLayoutEffect(() => {
    openRef.current = openId;
  });
  const actions = useReviewActions(bidId, (action) => {
    setError(null);
    // Only this item's own edit or rejection closes its form: a slower answer to an
    // earlier action must not take away a form the person has just opened.
    if (
      ["edit", "reject"].includes(action.kind) &&
      openRef.current &&
      action.item_ids.includes(openRef.current)
    ) {
      setMode("view");
    }
    // Only what this action decided leaves the selection: a slow answer must not clear a
    // selection made while it was on its way.
    const decided = new Set(action.item_ids);
    setSelection((current) =>
      Object.fromEntries(Object.entries(current).filter(([id]) => !decided.has(id))),
    );
    post({ type: "changed" });
  });
  const busy =
    Object.values(actions).some((m) => typeof m !== "function" && m.isPending) ||
    Object.values(gate).some((m) => m.isPending);

  function fail(caught: unknown) {
    setError(caught instanceof Error ? caught.message : "That did not work");
  }

  function chooseSheet(id: string) {
    setLassoed(new Set());
    setSearch((params) => {
      params.set("sheet", id);
      return params;
    });
    post({ type: "sheet", sheetId: id });
  }

  /** Open an item: zoom to its evidence, switching sheet if its evidence is elsewhere. */
  function openItem(itemId: string, row?: QueueRow) {
    setOpenId(itemId);
    setMode("view");
    setError(null);
    const item = (row ?? rows.find((r) => r.item.id === itemId))?.item;
    const boxes = item?.evidence_boxes ?? [];
    const here = boxes.find((b) => b.sheet_id === sheetId) ?? boxes[0];
    if (here) {
      const target = String(here.sheet_id);
      if (target !== sheetId) chooseSheet(target);
      const box = here.box as number[];
      setFocus({ box, key: Date.now() });
      post({ type: "focus", sheetId: target, box });
    }
  }
  useLayoutEffect(() => {
    opener.current = openItem;
  });

  // What the viewer highlights: the open item's marks, and anything lassoed.
  const highlighted = useMemo(() => {
    const ids = new Set(lassoed);
    if (openId) {
      for (const mark of overlay.index.marks) if (mark.item_id === openId) ids.add(mark.id);
    }
    return ids;
  }, [lassoed, openId, overlay.index]);

  useEffect(() => {
    post({ type: "selected", ids: [...highlighted] });
  }, [highlighted, post]);

  function onPick(mark: Mark | null, additive: boolean) {
    if (additive && mark) {
      setLassoed((current) => pickInto(current, mark.id, true));
      return;
    }
    setLassoed(new Set());
    if (mark?.item_id) openItem(mark.item_id);
  }

  const lassoMarks = overlay.index.marks.filter((m) => lassoed.has(m.id));
  const lassoItems = [...new Set(lassoMarks.map((m) => m.item_id).filter(Boolean))] as string[];

  // Keyboard: A accept, E edit, R reject, J/K next and previous, Ctrl+Z undo.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "SELECT", "TEXTAREA"].includes(target.tagName)) return;
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
        actions
          .undoLast()
          .then((undone) => {
            if (undone) return undone;
            const last = recent.data?.find((a) => !a.undone && a.kind !== "undo");
            return last ? actions.undo.mutateAsync(last.id) : null;
          })
          .catch(fail);
        event.preventDefault();
        return;
      }
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      const key = event.key.toLowerCase();
      const index = rows.findIndex((r) => r.item.id === openId);
      if (key === "j" || key === "k") {
        const next = rows[Math.max(0, Math.min(rows.length - 1, index + (key === "j" ? 1 : -1)))];
        if (next) opener.current(next.item.id);
      } else if (open && key === "a" && ["proposed", "edited"].includes(open.item.state)) {
        actions.accept.mutateAsync([open.item.id]).catch(fail);
      } else if (open && key === "e") {
        setMode("edit");
      } else if (open && key === "r") {
        setMode("reject");
      } else {
        return;
      }
      event.preventDefault();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rows, openId, open, recent.data, actions]);

  function stopManual() {
    setManual(null);
    setPlaced([]);
    setTool("select");
  }

  function saveManual(points: number[][], kind: "count" | "length") {
    if (!manual || !sheet || points.length === 0) return;
    const view = viewAt(sheet, points[0]!);
    if (!view || !view.measurable) {
      setError("Place them inside a view whose scale is verified or calibrated.");
      return;
    }
    if (points.some((p) => viewAt(sheet, p)?.id !== view.id)) {
      setError("Keep one item's marks inside one view.");
      return;
    }
    const geometry = { view_id: view.id, points };
    actions.createManual
      .mutateAsync({
        item_type: manual.type.key,
        description: manual.description,
        unit: kind === "count" ? "no" : "m",
        attributes: manual.attributes,
        ...(kind === "count" ? { marks: geometry } : { measure: geometry }),
      })
      .then((created) => {
        stopManual();
        post({ type: "changed" });
        openItem(created.id);
      })
      .catch(fail);
  }

  function onLength(points: number[][]) {
    if (calibrating) {
      setCalibrating({ ...calibrating, points: [points[0]!, points[points.length - 1]!] });
      setTool("select");
      return;
    }
    if (manual?.kind === "length") {
      saveManual(points, "length");
      return;
    }
    setTool("select");
    if (!remeasure || !open || !sheet) return;
    const view = viewAt(sheet, points[0]!);
    if (!view) {
      setError("Those points are outside every view on this sheet.");
      return;
    }
    const change: EditInput = {
      reason_code: remeasure.reason,
      note: remeasure.note || null,
      measure: { view_id: view.id, points },
    };
    setRemeasure(null);
    actions.edit.mutateAsync({ itemId: open.item.id, change }).catch(fail);
  }

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

  const levels = [...new Set(rows.map((r) => r.item.level).filter(Boolean))] as string[];
  const types = [...new Set(rows.map((r) => r.item.item_type))].sort();

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
        <UndoButton
          last={recent.data?.find((a) => !a.undone && a.kind !== "undo") ?? null}
          busy={busy}
          onUndo={(id) => actions.undo.mutateAsync(id).catch(fail)}
        />
        {tool === "length" && (
          <p role="status" className="text-sm text-orange-700">
            Measuring: click along the pipe, double-click or Enter to finish, Escape to stop.
          </p>
        )}
      </div>

      <div className="grid gap-3 lg:grid-cols-[13rem_1fr_28rem]">
        <aside className="max-h-[calc(100vh-9rem)] overflow-auto rounded-lg border p-3">
          <LayerPanel marks={overlay.index.marks} layers={layers} onChange={setLayers} />
        </aside>

        <div>
          {tiles.data && sheet?.width_mm ? (
            <DrawingViewer
              source={tiles.data}
              widthMm={sheet.width_mm}
              index={overlay.index}
              layers={layers}
              selected={highlighted}
              focus={focus}
              tool={tool}
              onPick={onPick}
              onLasso={(marks) => setLassoed(new Set(marks.map((m) => m.id)))}
              onLength={onLength}
              onCount={(point) => setPlaced((current) => [...current, point])}
              pending={manual?.kind === "count" ? placed : []}
            />
          ) : (
            <p role="status" className="text-sm text-muted-foreground">
              {tiles.isPending ? "Opening the sheet…" : "This sheet has not finished being read."}
            </p>
          )}
        </div>

        <aside className="flex h-[calc(100vh-9rem)] min-h-[32rem] flex-col gap-3 overflow-y-auto">
          <nav className="flex gap-1 text-sm" aria-label="Workbench panels">
            {(
              [
                ["queue", "Queue"],
                ["duplicates", `Duplicates (${(duplicates.data ?? []).filter((g) => g.status === "unresolved").length})`],
                ["setup", "Symbols & scale"],
                ["g1", "Coverage & G1"],
              ] as [Tab, string][]
            ).map(([key, label]) => (
              <button
                key={key}
                type="button"
                aria-pressed={tab === key}
                className={`rounded px-2 py-1 ${tab === key ? "bg-accent font-medium" : "hover:bg-accent/50"}`}
                onClick={() => setTab(key)}
              >
                {label}
              </button>
            ))}
          </nav>
          {tab === "queue" && (
            <>
          <ManualTools
              sheet={sheet}
              types={objectTypes.data ?? []}
              active={manual}
              placed={placed.length}
              busy={busy}
              error={manual ? error : null}
              onStart={(draft) => {
                setError(null);
                setManual(draft);
                setPlaced([]);
                setTool(draft.kind === "count" ? "count" : "length");
              }}
              onSave={() => saveManual(placed, "count")}
              onCancel={stopManual}
            />
            {lassoMarks.length > 0 && (
              <LassoPanel
                marks={lassoMarks}
                items={lassoItems}
                reasons={reasons.data ?? []}
                busy={busy}
                onAccept={() => actions.accept.mutateAsync(lassoItems).catch(fail)}
                onRejectDetections={(reason, note) =>
                  actions.rejectDetections
                    .mutateAsync({
                      detectionIds: lassoMarks.filter((m) => m.kind !== "manual").map((m) => m.id),
                      reason,
                      note,
                    })
                    .then(() => setLassoed(new Set()))
                    .catch(fail)
                }
                onClear={() => setLassoed(new Set())}
              />
            )}
            <div className="rounded-lg border p-2">
              {queue.isError ? (
                <p role="alert" className="text-sm text-destructive">
                  Could not load the review queue.
                </p>
              ) : (
                <QueuePanel
                  rows={rows}
                  filters={filters}
                  onFilters={setFilters}
                  levels={levels}
                  types={types}
                  openId={openId}
                  onOpen={(row) => openItem(row.item.id, row)}
                  selection={selection}
                  onSelection={setSelection}
                  reasons={reasons.data ?? []}
                  busy={busy}
                  onAccept={(ids) => actions.accept.mutateAsync(ids).catch(fail)}
                  onReject={(ids, reason) =>
                    actions.reject.mutateAsync({ itemIds: ids, reason }).catch(fail)
                  }
                />
              )}
            </div>
            {open && (
              <div className="shrink-0 rounded-lg border p-3">
                <ItemPanel
                  item={open.item}
                  evidence={evidence.data}
                  reasons={reasons.data ?? []}
                  busy={busy}
                  error={error}
                  mode={mode}
                  onMode={setMode}
                  onAccept={() => actions.accept.mutateAsync([open.item.id]).catch(fail)}
                  onEdit={(change) =>
                    actions.edit.mutateAsync({ itemId: open.item.id, change }).catch(fail)
                  }
                  onReject={(reason, note) =>
                    actions.reject
                      .mutateAsync({ itemIds: [open.item.id], reason, note })
                      .catch(fail)
                  }
                  onRemeasure={(reason, note) => {
                    setRemeasure({ reason, note });
                    setTool("length");
                  }}
                  onZoom={() => openItem(open.item.id, open)}
                />
              </div>
            )}
            </>
          )}
          {tab === "duplicates" && (
            <div className="min-h-0 flex-1 overflow-auto rounded-lg border p-2">
              <DuplicatesPanel
                bidId={bidId}
                groups={duplicates.data ?? []}
                sheets={sheets.data}
                busy={busy}
                error={error}
                onDecide={(groupId, decision) =>
                  gate.decide
                    .mutateAsync({ groupId, decision })
                    .then(() => post({ type: "changed" }))
                    .catch(fail)
                }
              />
            </div>
          )}
          {tab === "setup" && (
            <div className="min-h-0 flex-1 overflow-auto rounded-lg border p-2">
              <SetupPanel
                blockers={blockers.data}
                types={objectTypes.data ?? []}
                sheet={sheet}
                busy={busy}
                error={error}
                calibrating={calibrating}
                onName={(symbolKey, lineageId, objectType) =>
                  gate.nameSymbol.mutateAsync({ symbolKey, lineageId, objectType }).catch(fail)
                }
                onStartCalibration={(viewId) => {
                  setCalibrating({ viewId, points: [] });
                  setTool("length");
                }}
                onCalibrate={(distanceMm) => {
                  if (!calibrating || !sheetId) return;
                  gate.calibrate
                    .mutateAsync({
                      sheetId,
                      viewId: calibrating.viewId,
                      points: calibrating.points,
                      distanceMm,
                    })
                    .then(() => setCalibrating(null))
                    .catch(fail);
                }}
                onCancelCalibration={() => {
                  setCalibrating(null);
                  setTool("select");
                }}
              />
            </div>
          )}
          {tab === "g1" && (
            <div className="min-h-0 flex-1 overflow-auto rounded-lg border p-2">
              <GatePanel
                coverage={coverage.data}
                blockers={blockers.data}
                canApprove={canApprove}
                busy={busy}
                error={error}
                approval={approval}
                onGo={(next, status) => {
                  setTab(next);
                  if (status) setFilters({ ...filters, status });
                }}
                onOpenItem={(itemId) => {
                  setTab("queue");
                  openItem(itemId);
                }}
                onApprove={(comment) =>
                  gate.approve
                    .mutateAsync(comment)
                    .then(setApproval)
                    .catch(fail)
                }
              />
            </div>
          )}
        </aside>
      </div>
    </section>
  );
}

function viewAt(sheet: WorkbenchSheet, point: number[]) {
  const [x, y] = point as [number, number];
  return (
    sheet.views.find(
      (v) => v.extent[0]! <= x && x <= v.extent[2]! && v.extent[1]! <= y && y <= v.extent[3]!,
    ) ?? null
  );
}

function UndoButton({
  last,
  busy,
  onUndo,
}: {
  last: { id: string; kind: string; count: number } | null;
  busy: boolean;
  onUndo: (id: string) => void;
}) {
  if (!last) return null;
  return (
    <button
      type="button"
      className="rounded border px-2 py-1 text-sm disabled:opacity-50"
      disabled={busy}
      onClick={() => onUndo(last.id)}
      title="Ctrl+Z"
    >
      Undo {last.kind.replaceAll("_", " ")} ({last.count})
    </button>
  );
}

function LassoPanel({
  marks,
  items,
  reasons,
  busy,
  onAccept,
  onRejectDetections,
  onClear,
}: {
  marks: Mark[];
  items: string[];
  reasons: { code: string; label: string }[];
  busy: boolean;
  onAccept: () => void;
  onRejectDetections: (reason: string, note: string) => void;
  onClear: () => void;
}) {
  const [rejecting, setRejecting] = useState(false);
  return (
    <section
      aria-label="Selection on the drawing"
      className="space-y-2 rounded-lg border p-2 text-sm"
    >
      <p className="font-medium">
        {marks.length} marks selected on the drawing · {items.length} items
      </p>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          className="rounded bg-green-600 px-2 py-1 text-white disabled:opacity-50"
          disabled={!items.length || busy}
          onClick={onAccept}
        >
          Accept their {items.length} items
        </button>
        <button
          type="button"
          className="rounded border px-2 py-1 text-red-700"
          onClick={() => setRejecting(true)}
        >
          Not there…
        </button>
        <button type="button" className="rounded border px-2 py-1" onClick={onClear}>
          Clear
        </button>
      </div>
      {rejecting && (
        <ReasonForm
          title={`Reject ${marks.length} detections`}
          reasons={reasons}
          busy={busy}
          onCancel={() => setRejecting(false)}
          onSubmit={(reason, note) => {
            setRejecting(false);
            onRejectDetections(reason, note);
          }}
        />
      )}
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
