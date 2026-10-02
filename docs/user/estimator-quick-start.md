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

**Sending a whole folder.** Use **Or send a whole folder** to pick the tender folder as it sits on your drive. Before anything is sent, FireBid lists each folder with what it thinks is in it:

- **Tender documents:** what the client issued. Only these are read and taken off.
- **Our working documents:** your own marked-up drawings. Kept with the bid, not read. This matters: a marked-up copy carries the client's drawing number, and read as a tender drawing it could replace the client's own sheet.
- **Reference:** earlier responses, reviews and registers. Kept, not read.
- **Leave out:** not sent.

Change any folder that is wrong, then send. Files go in batches and every one is accounted for at the end. A file over 200 MB is named, not sent: if it is a zip of the same folder, you do not need it. A document found inside a zip that looks like one of yours is kept unread until you say whose it is.

## 3. Confirm the symbols

Go to **Symbols**. FireBid reads the legend sheets and proposes a meaning for each symbol, such as "upright sprinkler, 68 °C".

- **Legend rows it has matched before** for this consultant are reused.
- **New rows** are proposed by rules or by the model. Check each one against the legend, then confirm it or correct it.
- **Unnamed symbols** appear on plans but in no legend. Name the ones that matter; the rest are listed, not counted.

Detection runs again every time you confirm a mapping. Symbols you have not confirmed are never counted.

### If the tender is drawn as design intent

Some tenders show the mains and leave the heads and range pipes to you ("the contractor shall be responsible for the further development and detailed design"). A takeoff of what is drawn then counts no heads. Go to **Design development**:

1. **Read the design basis.** FireBid lists each plan sheet with the design criteria its notes state, such as "4 m × 3 m, 12 m² a head".
2. **Confirm the criterion.** A senior estimator or design manager picks the criterion for the selected sheets. Nothing is laid out before this.
3. **Check the proposal.** FireBid proposes heads and range pipes for each room. They appear on the Workbench like any other item, marked *proposed layout, not drawn*, and you accept or reject them there. Each sheet lists the spaces it left without heads (lifts, shafts, stairs) and the rule that left them out.

Things to know:

- **It is an estimating aid, not a design.** It does no hydraulic calculation and is not for submission or construction.
- **The design rules start as defaults** and say "to be confirmed" until a senior estimator has reviewed them (grid, omissions, pipe sizing).
- **A sheet needs a verified scale.** Calibrate the view first if the sheet is shown as *cannot be laid out*.
- **Sheets that share a floor** across match lines are each laid out in full. Resolve the duplicate groups, and check the shared area by eye.
- **A sheet with heads already drawn** is flagged. Do not confirm it, or those heads are counted twice.

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
