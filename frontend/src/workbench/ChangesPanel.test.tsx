import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { changeMarks, type RevisionDiff } from "./changes";
import { ChangesPanel } from "./ChangesPanel";
import { STATUS_COLOURS } from "./marks";

const DIFF: RevisionDiff = {
  old: { sheet_number: "FP-L05-201", revision: "R01" },
  new: { sheet_number: "FP-L05-201", revision: "R02" },
  alignment: { method: "grid", matched: 0.96 },
  counts: { added: 1, removed: 1, changed: 2, unchanged: 41 },
  changes: [
    {
      change: "changed",
      kind: "run",
      object_type: "pipe_branch",
      x: 100,
      y: 190,
      old_id: "a",
      new_id: "b",
      before: { dn: 50 },
      after: { dn: 65 },
      points: [
        [100, 250],
        [100, 130],
      ],
    },
    {
      change: "changed",
      kind: "object",
      object_type: "sprinkler_pendent",
      x: 190,
      y: 190,
      old_id: "c",
      new_id: "d",
      before: { object_type: "sprinkler_upright" },
      after: { object_type: "sprinkler_pendent" },
      points: [],
    },
    {
      change: "added",
      kind: "object",
      object_type: "sprinkler_pendent",
      x: 70,
      y: 205,
      old_id: null,
      new_id: "e",
      before: null,
      after: { object_type: "sprinkler_pendent" },
      points: [],
    },
    {
      change: "removed",
      kind: "object",
      object_type: "sprinkler_sidewall",
      x: 220,
      y: 130,
      old_id: "f",
      new_id: null,
      before: { object_type: "sprinkler_sidewall" },
      after: null,
      points: [],
    },
  ],
};

const DELTA = {
  baseline: { reason: "before Addendum 1", taken_at: "2026-07-01T00:00:00Z" },
  counts: {
    added: 1,
    removed: 0,
    changed: 4,
    unchanged: 11,
    verification_kept: 11,
    to_review: 5,
  },
  items: [
    {
      change: "changed",
      human_id: "QTO-000004",
      description: "Sprinkler, pendent",
      unit: "no",
      before: "16.000",
      after: "18.000",
      difference: "2.000",
      state_after: "proposed",
      to_review: true,
    },
  ],
  lines: [
    {
      line: "A1 Pendent sprinkler head",
      unit: "nr",
      before: "16.000",
      after: "18.000",
      difference: "2.000",
    },
  ],
  gate: { status: "reopened", to_verify: ["QTO-000004"] },
};

// req: FR-DOC-08
describe("the revision comparison", () => {
  it("lists every change with what it was and what it is", () => {
    render(
      <ChangesPanel
        diff={DIFF}
        delta={null}
        loading={false}
        onFocus={() => {}}
        onOpenItem={() => {}}
      />,
    );

    const list = screen.getByRole("list", { name: "Changes on this sheet" });
    const rows = within(list)
      .getAllByRole("listitem")
      .map((row) => row.textContent);
    expect(rows).toEqual([
      "Changedpipe branch: dn 50 → dn 65",
      "Changedsprinkler pendent: object type sprinkler upright → object type sprinkler pendent",
      "Addedsprinkler pendent",
      "Removedsprinkler sidewall",
    ]);
    expect(
      screen.getByText(
        /R01 → R02: 1 added, 1 removed, 2 changed, 41 unchanged/,
      ),
    ).toBeTruthy();
    expect(screen.getByText(/Aligned by grid/)).toBeTruthy();
  });

  it("draws each change on the newer sheet, coloured by what changed", () => {
    const marks = changeMarks(DIFF.changes);

    expect(marks.map((m) => m.status)).toEqual([
      "changed",
      "changed",
      "added",
      "removed",
    ]);
    expect(marks[0]!.box).toEqual([100, 130, 100, 250]); // the run, end to end
    expect(marks[2]!.box).toEqual([67.5, 202.5, 72.5, 207.5]); // 5 mm about the new head
    for (const mark of marks) expect(STATUS_COLOURS[mark.status]).toBeTruthy();
  });

  it("takes the drawing to a change that is clicked", async () => {
    const onFocus = vi.fn();
    render(
      <ChangesPanel
        diff={DIFF}
        delta={null}
        loading={false}
        onFocus={onFocus}
        onOpenItem={() => {}}
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: /Removed/ }));

    expect(onFocus).toHaveBeenCalledWith([217.5, 127.5, 222.5, 132.5]);
  });

  it("says so when the sheet replaced no earlier revision", () => {
    render(
      <ChangesPanel
        diff={null}
        delta={null}
        loading={false}
        onFocus={() => {}}
        onOpenItem={() => {}}
      />,
    );

    expect(screen.getByText(/replaced no earlier revision/)).toBeTruthy();
    expect(screen.getByText(/No baseline yet/)).toBeTruthy();
  });
});

// req: FR-QTO-12
describe("the delta report", () => {
  it("shows each changed item's value before, the BOQ line's change, and the reopened gate", async () => {
    const onOpenItem = vi.fn();
    render(
      <ChangesPanel
        diff={null}
        delta={DELTA}
        loading={false}
        onFocus={() => {}}
        onOpenItem={onOpenItem}
      />,
    );

    const table = screen.getByRole("table", { name: "Changed items" });
    const cells = within(table)
      .getAllByRole("cell")
      .map((cell) => cell.textContent);
    expect(cells).toEqual([
      "QTO-000004 Sprinkler, pendentto verify",
      "16.000",
      "18.000",
      "+2.000 no",
    ]);
    expect(screen.getByRole("alert").textContent).toContain(
      "G1 is reopened: 1 item(s) to verify again",
    );
    expect(
      screen.getByText(
        /A1 Pendent sprinkler head: 16.000 → 18.000 nr \(\+2.000\)/,
      ),
    ).toBeTruthy();
    expect(screen.getByText(/11 kept their verification/)).toBeTruthy();

    await userEvent.click(screen.getByRole("button", { name: /QTO-000004/ }));
    expect(onOpenItem).toHaveBeenCalledWith("QTO-000004");
  });
});
