import {
  changeMarks,
  type Delta,
  describe,
  type RevisionDiff,
} from "./changes";

/**
 * The Changes panel (FR-DOC-08, FR-QTO-12): what this sheet's revision changed, listed and
 * drawn over the drawing; and what changed in the takeoff since its baseline, with each
 * changed item's value before and where G1 stands.
 */

const LABELS: Record<string, string> = {
  added: "Added",
  removed: "Removed",
  changed: "Changed",
};

export function ChangesPanel({
  diff,
  delta,
  loading,
  onFocus,
  onOpenItem,
}: {
  diff: RevisionDiff | null | undefined;
  delta: Delta | null | undefined;
  loading: boolean;
  onFocus: (box: number[]) => void;
  onOpenItem: (humanId: string) => void;
}) {
  const marks = diff ? changeMarks(diff.changes) : [];
  return (
    <section aria-label="Changes" className="space-y-4 text-sm">
      <div className="space-y-2">
        <h3 className="font-medium">
          This sheet against the revision it replaced
        </h3>
        {loading && <p role="status">Comparing revisions…</p>}
        {!loading && !diff && (
          <p className="text-muted-foreground">
            This sheet replaced no earlier revision: there is nothing to compare
            it with.
          </p>
        )}
        {diff && (
          <>
            <p>
              {diff.new.sheet_number} {diff.old.revision} → {diff.new.revision}:{" "}
              {diff.counts.added ?? 0} added, {diff.counts.removed ?? 0}{" "}
              removed, {diff.counts.changed ?? 0} changed,{" "}
              {diff.counts.unchanged ?? 0} unchanged.
            </p>
            <p className="text-xs text-muted-foreground">
              Aligned by {diff.alignment.method}; shown over the drawing in
              place of the takeoff.
            </p>
            <ul
              aria-label="Changes on this sheet"
              className="divide-y rounded border"
            >
              {diff.changes.map((change, index) => (
                <li key={index}>
                  <button
                    type="button"
                    className="flex w-full items-baseline gap-2 px-2 py-1 text-left hover:bg-accent/50"
                    onClick={() => onFocus(marks[index]!.box)}
                  >
                    <span
                      className={`w-16 shrink-0 font-medium change-${change.change}`}
                    >
                      {LABELS[change.change] ?? change.change}
                    </span>
                    <span>{describe(change)}</span>
                  </button>
                </li>
              ))}
              {diff.changes.length === 0 && (
                <li className="px-2 py-1">Nothing changed.</li>
              )}
            </ul>
          </>
        )}
      </div>

      <div className="space-y-2">
        <h3 className="font-medium">The takeoff against its baseline</h3>
        {!delta && (
          <p className="text-muted-foreground">
            No baseline yet: one is taken when G1 is approved and when an
            addendum is registered.
          </p>
        )}
        {delta && (
          <>
            <p>
              Since “{delta.baseline.reason}”: {delta.counts.changed ?? 0}{" "}
              changed, {delta.counts.added ?? 0} added,{" "}
              {delta.counts.removed ?? 0} removed;{" "}
              {delta.counts.verification_kept ?? 0} kept their verification.
            </p>
            {delta.gate.status === "reopened" && (
              <p
                role="alert"
                className="rounded border border-amber-500 bg-amber-50 p-2 text-amber-900"
              >
                G1 is reopened: {(delta.gate.to_verify ?? []).length} item(s) to
                verify again.
              </p>
            )}
            <table className="w-full text-left" aria-label="Changed items">
              <thead>
                <tr className="text-xs text-muted-foreground">
                  <th>Item</th>
                  <th>Before</th>
                  <th>Now</th>
                  <th>Change</th>
                </tr>
              </thead>
              <tbody>
                {delta.items.map((item) => (
                  <tr key={item.human_id} className="border-t">
                    <td>
                      <button
                        type="button"
                        className="text-left underline-offset-2 hover:underline"
                        onClick={() => onOpenItem(item.human_id)}
                      >
                        {item.human_id} {item.description}
                      </button>
                      {item.to_review && (
                        <span className="ml-1 text-xs text-amber-700">
                          to verify
                        </span>
                      )}
                    </td>
                    <td>{item.before ?? "—"}</td>
                    <td>{item.after ?? "—"}</td>
                    <td>
                      {Number(item.difference) > 0 ? "+" : ""}
                      {item.difference} {item.unit}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {delta.lines.length > 0 && (
              <ul aria-label="BOQ lines changed" className="space-y-1">
                {delta.lines.map((line) => (
                  <li key={`${line.line}|${line.unit}`}>
                    {line.line}: {line.before} → {line.after} {line.unit} (
                    {Number(line.difference) > 0 ? "+" : ""}
                    {line.difference})
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
    </section>
  );
}
