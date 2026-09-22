# P2-04 · Supplier Quotations and Cost Build-Up

**Builds on:** P1-10 (rate library, provenance enforcement), P1-09 (BOQ). **Needs:** sample supplier quotations, the company cost build-up template, FX policy and decision D4 (ERP) (business track). Fallback for ERP: file-based import.

Begin by replying with a plan that maps each item under **Done when** to the work that satisfies it. Wait for approval before changing files.

## Read first

- `docs/plan/project-context.md`: guardrails 3 and 4, money, configuration over constants.
- Requirements: §6.11 (FR-CST-02 to 09), §5 (G2).
- `docs/plan/BUILD_LOG.md`.

## Goal

Capture supplier quotations with full provenance, and price in SGD with FX, import costs and GST handled explicitly. Every price stays traceable and every cost component visible. The platform never generates a price.

## Scope

FR-CST-02, 03, 04, 05, 06, 07, 08, 09.

## Build

1. **Quotation capture** (FR-CST-02):
   - Ingest PDF, xlsx and email (.eml/.msg with attachments) into Quotation records.
   - Extraction via structured outputs: supplier, quote number and date, validity, currency, lines (description, brand/model, unit, unit price, MOQ, lead time), delivery terms and exclusions.
   - A confirmation UI shows extracted fields against the source document. Each line is linked to a rate-library item key or BOQ line.
2. **Provenance and validity** (FR-CST-03): every priced line references a rate entry, quotation line or PO line. Flag:
   - expired quotes;
   - quotes whose validity ends before the tender validity period;
   - quotes with material exclusions.
3. **Currency and import** (FR-CST-04): an FX rate table (rate, source, date) with a configurable buffer. Landed cost = FOB or CIF price × FX (+ buffer) + freight + insurance + import charges, each as a configurable line.
4. **GST** (FR-CST-05): the GST rate is configuration with an effective date. Prices are held exclusive of GST, and GST is computed on totals and shown separately.
5. **Cost build-up** (FR-CST-06): separate, visible lines for:
   - materials, fittings, valves, equipment;
   - labour (placeholder until P2-05), supervision, access equipment;
   - testing and commissioning, transport, subcontract, wastage;
   - site overheads, preliminaries, insurance, bonds;
   - contingency and margin.

   Each line has its basis (percentage, lump sum, calculated) and source. A summary view shows totals by component.
6. **Historical comparison** (FR-CST-07): compare each price with historical PO and project rates for the same item key, and flag outliers beyond a configurable tolerance with the comparison shown.
7. **ERP** (FR-CST-08): an adapter interface with a file-based importer (item master, POs, historical costs) now; an API adapter comes when D4 names the system.
8. **No generated prices** (FR-CST-09): price fields require a source foreign key at the database and domain layers. Where no source exists, a line is "unpriced" or carries an estimator allowance labelled with the estimator's name. A G2 check fails on any unsourced priced line.

## Done when

- Quotation fixtures (PDF, xlsx, email) extract into confirmed quotations with every listed field. Tagged FR-CST-02.
- Tests flag an expired quote, a quote with validity shorter than tender validity, and a quote with exclusions. Tagged FR-CST-03.
- A USD quote converts with the recorded FX rate, buffer and import lines, matching a hand calculation. Tagged FR-CST-04.
- GST is computed from configuration, and changing the effective-dated rate changes only bids priced after that date. Tagged FR-CST-05.
- The build-up shows every component as a separate line with its basis. Tagged FR-CST-06.
- An outlier price is flagged against history. Tagged FR-CST-07.
- The ERP file import loads a sample. Tagged FR-CST-08.
- The G2 check fails with an unsourced priced line and passes once it is sourced or marked unpriced. Tagged FR-CST-09.
- A build log entry is appended.
