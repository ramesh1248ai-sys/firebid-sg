# FireBid SG: estimator quick start

FireBid reads a fire protection tender's drawings and documents and proposes quantities. You check those quantities, then build and price the BOQ. You stay in charge: nothing the AI proposes counts until a person accepts it, and every number traces back to where it came from on a drawing.

The basic flow:

1. Create the bid.
2. Upload the tender documents.
3. Confirm the symbols.
4. Verify the takeoff and pass G1.
5. Build and price the BOQ, and pass G2.

## 1. Create the bid

Sign in with your company account, then choose **New bid**. You need:

- the project name, client and tender reference;
- the **submission deadline**;
- the **consultant** whose drawings these are, if you know them.

**Why the deadline matters:**

- You get deadline alerts.
- No maintenance is planned within 48 hours of it.

**Why the consultant matters:** FireBid learns each consultant's symbols. A consultant you have worked with before needs fewer confirmations.

## 2. Upload the tender

Open the bid, go to **Tender documents**, and upload the whole set at once: drawings (PDF or DXF), specification, BOQ and drawing list (Excel). Duplicates are recognised and skipped.

- **Scanning.** Every file is scanned for malware before anything opens it. A file shown as *awaiting scan* is held, not lost. Retry it later.
- **Reading.** Drawings are read in the background: sheets, title blocks, views and scales, symbols, then detections. A large set takes a while, and you can keep working.
- **Registers.** Check them for the drawing register, revisions and missing sheets. A sheet on the drawing list but not in the upload is flagged.

## 3. Confirm the symbols

Go to **Symbols**. FireBid reads the legend sheets and proposes a meaning for each symbol, such as "upright sprinkler, 68 °C".

- **Legend rows it has matched before** for this consultant are reused.
- **New rows** are proposed by rules or by the model. Check each one against the legend, then confirm it or correct it.
- **Unnamed symbols** appear on plans but in no legend. Name the ones that matter; the rest are listed, not counted.

Detection runs again every time you confirm a mapping. Symbols you have not confirmed are never counted.

## 4. Verify the takeoff (G1)

Go to **Workbench**, which has the drawing on one side and the review queue on the other.

- **Review queue:**
  - Each item shows the sheet, grid reference and confidence, with its evidence highlighted on the drawing.
  - **Accept** or **reject** each item. A rejection needs a reason.
  - Add anything the AI missed with **Manual takeoff**. It is marked as yours, and it counts toward the missed-item measure.
- **Duplicate groups** are the same item seen twice: on an enlarged plan, a section, or overlapping match lines. Resolve every group. Unresolved duplicates block G1.
- **Coverage and G1** shows how much of the takeoff is verified. In Phase 1, every item must be verified.
- **What blocks G1** lists exactly what is still open.

When nothing blocks it, the senior estimator passes **G1: QTO verified**.

## 5. Build and price the BOQ (G2)

Go to **BOQ**.

- **Our BOQ** is built from the verified takeoff. Every line traces to its quantities. A line with no trace, such as a provisional or lump sum, must be marked as such.
- **Reconciliation** compares our BOQ with the client's BOQ where one was issued: lines missing, extra, or with different quantities.
- **Pricing:**
  - Rates come from the rate library (**Rates**, kept by the senior estimator).
  - An exact match prices a line directly.
  - A proposed match needs your confirmation.
  - A line with no rate stays unpriced and is listed, never priced at zero.
  - Warnings flag rates that are stale or expire before the tender's validity ends.
- **Grand total** shows the totals.

The senior estimator reviews and approves the estimate at **G2: Estimate approved**.

## Good to know

- **Everything is audited.** Every accept, reject, confirmation and approval is recorded, with who and when, in a tamper-evident log (**Audit**).
- **If the AI is unavailable,** the work falls back to another approved model or is handed to you to do by hand. Nothing is lost, and you are told.
- **KPIs** (senior estimators) show per bid:
  - false detections;
  - missed items;
  - open duplicates;
  - time on task;
  - what the AI cost.
- **Confidential data stays confidential.** Tender documents are only visible to people on the bid.
- **Stuck?** Tell the platform team which bid and what you were doing. For a bid due within 48 hours, say so first.
