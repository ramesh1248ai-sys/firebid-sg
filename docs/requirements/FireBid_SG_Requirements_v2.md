# FireBid SG — Business & Solution Requirements Specification v2.2

<!-- Generated from FireBid_SG_Agentic_AI_Requirements_v2.docx (v2.2). The .docx is the approved source; regenerate this file when the .docx changes. -->

## 0. Refinement Summary

### 0.1 Overall Assessment of v1

The v1 baseline is a strong solution-scope document. It positions the platform correctly (AI-assisted, not an autonomous fire-safety design or approval system), puts traceability and evidence at the centre, sets sound guardrails and proposes a sensible phased roadmap.

It is not yet a requirements baseline that can be estimated, contracted, built and accepted. Requirements are not uniquely identified, prioritised or testable. Users and decision rights are undefined. KPIs have no baselines or targets. Non-functional, security and data-usage requirements are missing, and so is Singapore-specific commercial context. The MVP conflicts with the Phase 1 roadmap. Some terminology follows US (NFPA) practice rather than Singapore practice. This v2 addresses those gaps while keeping the intent of v1 intact (see Appendix C for section-by-section traceability).

### 0.2 Key Review Findings

| # | Finding in v1 | Severity | Resolution in v2 |
|---|---|---|---|
| 1 | Requirements have no IDs, priorities, phases or acceptance criteria, so they cannot be estimated, tested or signed off. | High | Identified, prioritised (MoSCoW) and phased requirements with acceptance criteria (§6). |
| 2 | MVP (v1 §23) includes material pricing and labour, but the Phase 1 roadmap (v1 §24) defers them to Phase 2. | High | One MVP definition aligned with Phase 1 (§13.1); decision D1. |
| 3 | No personas or decision rights. "Qualified professionals approve" cannot be acted on. | High | Personas, RACI and named approval gates G0–G4 (§3, §5). |
| 4 | KPIs listed without definitions, baselines or targets. | High | KPI definitions and proposed targets; baseline set in Phase 0 (§14). |
| 5 | No non-functional, security, data residency or data-usage requirements. Tender documents are confidential third-party material. | High | NFRs (§10) and a data ownership and AI-usage policy (§11.4). |
| 6 | Human verification is the core control, yet no verification user experience is specified. | High | New Verification Workbench module (§6.5). |
| 7 | No support for pricing a client-issued BOQ or reconciling measured quantities against it, which is how most Singapore sub-contract tenders are priced. | High | BOQ mapping and quantity reconciliation (§6.6). |
| 8 | 2D measurement gaps not addressed: scale calibration, vertical pipework (drops/risers) not shown on plans, undrawn fittings, and enlarged plans that repeat general plans. | High | Measurement rules, scale gate and de-duplication (§6.3, §6.4). |
| 9 | One linear "approval state machine" mixes the contractor's bid workflow with external design and SCDF approvals the contractor does not own. | Medium | Five separate state models (§7). |
| 10 | Agent catalogue is inconsistent. Specification, Labour and Bid Risk agents are missing from the swarm table, names differ between sections, and "swarm" implies uncontrolled autonomy. | Medium | Unified, orchestrated agent catalogue with autonomy levels (§8). |
| 11 | US terminology (standpipe, FDC, OS&Y, seismic restraint) and a duplicated FDC entry. | Medium | Singapore terminology (§2.5, §4). NFPA/FM treated as project-specific. |
| 12 | Addenda tracked but not re-measured. No delta takeoff on revisions. | Medium | Revision comparison and delta QTO (§6.2, §6.4). |
| 13 | Singapore cost build-up missing: GST, foreign-currency equipment, foreign worker levy and accommodation, quote validity vs tender validity. | Medium | Costing and labour requirements (§6.11, §6.12). |
| 14 | No review of contract conditions (LDs, retention, bonds, design liability, back-to-back terms). | Medium | Commercial risk requirements (§6.13). |
| 15 | No bid/no-bid step and no post-tender feedback, so the "learn from actuals" requirements have no data source. | Medium | Gated process with post-tender loop (§5) and learning requirements (§6.15). |
| 16 | Guardrails are stated as intentions, not verifiable controls. | Medium | Guardrail control matrix with tests (§9). |
| 17 | No assumptions, dependencies, risk register or open decisions. | Medium | §15, §16 and §17. |
| 18 | Technology choices written as requirements. | Low | Recast as non-binding recommendations subject to architecture decisions (§12). |

### 0.3 Decisions Required from the Sponsor

The following decisions gate the start of Phase 0 / Phase 1 (full list in §17):

- **D1** Confirm the MVP scope: wet-pipe sprinkler QTO and BOQ from vector PDF and DWG, with rate-library pricing as a Should. Labour estimating moves to Phase 2.
- **D2** Approve cloud hosting in a Singapore region, and each LLM provider's data terms (region, retention, no-training), including which data classes each provider may receive.
- **D3** Nominate a data owner and provide 10–20 historical tenders with verified QTO/BOQ as the golden evaluation dataset.
- **D4** Confirm the ERP/accounting system and whether historical cost and labour-hour data exists.
- **D5** Name the accountable approvers for gates G0–G4.

## 1. Introduction

### 1.1 Purpose and Product Requirement Statement

This document defines the business and solution requirements for FireBid SG, an agentic AI platform for Singapore fire protection contractors. It is the baseline for estimation, design, build, test and acceptance.

**Product requirement:  **Develop an AI-assisted, agentic tendering and pre-construction platform for Singapore fire protection contractors. It converts tender documents into verified, evidence-traceable quantities, BOQs, estimates, clarifications and bid-risk assessments for sprinkler, fire pump, rising main, hydrant and hose reel systems. It keeps full drawing, revision and evidence traceability, and leaves every engineering, regulatory and commercial decision to accountable people.

### 1.2 Business Problem and Objectives

Fire protection contractors answer many tenders on short turnarounds. Manual takeoff from PDF drawings is labour-intensive, relies on scarce senior estimators, and is prone to missed items, double counting and use of superseded revisions. Most tenders are not won, so most estimating effort is never recovered, and missed scope often appears after award as cost that cannot be claimed back. Current-state figures (hours per tender, tenders per month, win rate, post-award scope losses) will be baselined in Phase 0.

| ID | Business objective | Proposed measure / target |
|---|---|---|
| O1 | Reduce estimator takeoff effort without reducing accuracy. | ≥30% fewer QTO hours per tender at Phase 1 exit; ≥60% at maturity. |
| O2 | Increase tender capacity per estimator. | Tenders submitted per estimator per month, up against baseline. |
| O3 | Reduce missed scope and quantity errors. | Missed-item rate ≤2% at maturity; zero BOQ lines without evidence. |
| O4 | Make every quantity, price and decision traceable and auditable. | 100% of BOQ lines trace to evidence and a named approver. |
| O5 | Improve estimate accuracy on awarded projects. | Estimate vs actual cost within ±10% (requires post-award data, Phase 4). |

All targets are proposals. They will be confirmed after the Phase 0 baseline.

### 1.3 Product Vision and Operating Principle

FireBid SG turns Singapore tender drawings, BIM models, specifications and project requirements into traceable quantity takeoffs, BOQs, cost estimates, coordination issues, tender clarifications and bid-ready documentation, with human approval for engineering, regulatory and commercial decisions.

**Operating principle.** The platform is an AI-assisted estimating and coordination system. It is not an autonomous fire-safety design or regulatory approval system. AI recommends; accountable professionals decide. The target operating model is: Tender → Bid/No-bid → QTO → Estimate → Coordination → Clarifications → Professional and commercial review → Submission → (post-award) handover to project delivery.

### 1.4 Scope Boundaries

| In scope | Out of scope (all phases unless re-baselined) | Future candidates (not committed) |
|---|---|---|
| Tender and pre-construction stage for the fire protection systems in §4. Singapore projects; English-language documents. Bid/no-bid support, document intelligence, QTO, BOQ, specification and compliance cross-checks, coordination, tender clarifications, pricing, labour, bid risk, tender package. | Fire-safety or hydraulic design, and design sign-off. Submissions to SCDF, CORENET X or any authority. Autonomous bid submission or contractual commitment. Changing approved design information. | Construction-stage RFIs and approval tracking. Fire alarm, gas suppression and other fire systems. Handover to project delivery (procurement, shop drawings, T&C records). |

Changes to these boundaries go through change control (§13.4).

## 2. Singapore Regulatory Context and Knowledge Governance

### 2.1 Accountabilities the Platform Must Respect

Legal and professional responsibilities stay with the parties below. The platform supports them and never acts for them:

- **Qualified Person (QP):** the registered architect or professional engineer responsible for fire safety plans submitted to SCDF.
- **Fire Safety Engineer (FSE):** responsible for performance-based fire engineering solutions, where used.
- **Registered Inspector (RI):** inspects completed fire safety works before the Fire Safety Certificate (FSC).
- **Consultants:** own the design intent and the tender documents.
- **SCDF:** the approving authority.
- **The contractor:** owns its quantities, prices, qualifications and commercial commitments.

### 2.2 Knowledge Sources

The Compliance Knowledge Agent (§8) retrieves and cites from a governed, version-pinned corpus. The corpus covers:

- The SCDF Fire Code (current edition, with earlier editions kept for projects still under them), plus SCDF circulars, amendments and plan-submission requirements.
- Relevant Singapore Standards and Codes of Practice, e.g. SS CP 52 (automatic fire sprinkler systems) and SS 575 (fire hydrant, rising mains and hose reel systems). Editions are recorded per project.
- Project specifications, approved drawings, approved waivers and consultation outcomes.
- Project-specific standards where the specification calls for them, e.g. NFPA or FM Global requirements on insured or data-centre projects.

### 2.3 Source-Type Labelling

Every compliance-related statement the platform produces must carry exactly one of these labels:

| Label | Meaning | May be presented as a requirement? |
|---|---|---|
| SCDF requirement | Clause of the Fire Code or an SCDF circular, with edition and clause cited. | Yes, with citation |
| Singapore Standard / CP | Clause of an SS or CP applicable to the project, with edition cited. | Yes, with citation |
| Project specification | Clause of the tender or contract specification. | Yes (project-binding) |
| Consultant / QP requirement | Documented instruction from the consultant or QP (drawing note, tender clarification response). | Yes, once documented |
| Contractor assumption | A position the contractor takes to price the work. | No. Shown as a qualification. |
| AI recommendation | An inference or suggestion by the platform. | No, never |

### 2.4 Rules

- No AI inference may be presented as a regulatory requirement.
- When sources conflict, the platform shows the conflict with every citation and does not resolve it. Precedence follows the contract's order-of-precedence clause as applied by a human reviewer.
- A named knowledge owner keeps the corpus current. Each document records edition, effective date, source and licence status.

### 2.5 Terminology Alignment

v1 used some US/NFPA terms. v2 uses Singapore terms. The US terms remain valid only where a project specification uses them.

| v1 term | v2 term | Note |
|---|---|---|
| Standpipe | Rising main (dry / wet) | Per SS 575 conventions. |
| Fire Department Connection (FDC) | Breeching inlet | v1 listed FDCs twice (under standpipes and separately); merged. |
| Hose valve | Landing valve | On rising mains. Hose reels are a separate system. |
| OS&Y valve | Gate / sluice valve (OS&Y where specified) | Valve type taken from the specification. |
| Alarm valve | Installation control valve set / alarm valve | Includes subsidiary / zone control valve assemblies. |
| Seismic restraint | Only where the project specifies it | Not a general Singapore requirement; appears in NFPA/FM-based specifications. |

## 3. Stakeholders, Users and Decision Rights

### 3.1 Personas

| Role | Description and key needs | Platform role |
|---|---|---|
| Estimator / QS | Primary user. Needs fast, reliable takeoff and one place to see drawings, quantities and prices. | Verifies QTO, prices BOQ, drafts qualifications |
| Senior Estimator / Estimating Manager | Owns estimate quality, measurement rules and productivity data. | Approves QTO (G1) and estimate (G2); owns libraries |
| Bid / Tender Manager | Owns deadlines, the bid team and client communications. | Runs the workflow; issues clarifications |
| Design / Engineering Manager | Fire protection engineer who reviews technical and compliance positions. | Approves engineering options and compliance findings |
| Commercial Director | Authorised approver for risk, margin and submission. | Approves bid/no-bid (G0), commercial position (G3) and submission (G4) |
| Procurement Executive | Obtains and maintains supplier quotations. | Captures quotes; maintains the rate library |
| Project Manager (downstream) | Receives the awarded job and supplies actual quantities and hours. | Handover recipient; feeds actuals |
| QP / Consultant (external) | Reviews issued clarifications. No platform access by default. | Reviewer outside the platform |
| System Administrator / Data Steward | Security, access, knowledge corpus and data governance. | Administers roles, corpus and retention |
| Executive Sponsor | Business owner accountable for benefits. | Chairs the steering committee |

### 3.2 Decision Rights (RACI)

R = Responsible, A = Accountable, C = Consulted, I = Informed. The Accountable role signs the gate in the platform.

| Decision | Estimator | Sr Estimator | Bid Mgr | Design Mgr | Comm. Dir | Procurement |
|---|---|---|---|---|---|---|
| Bid / no-bid (G0) | I | C | R | C | A | – |
| QTO verification (G1) | R | A | I | C | – | – |
| Measurement rules and allowances | C | A/R | I | C | – | – |
| Supplier price selection | C | A | I | – | I | R |
| Labour productivity adjustments | R | A | I | C | – | – |
| Engineering / fire-safety options | C | C | I | A/R | I | – |
| Regulatory interpretation with material consequence | I | C | I | A/R | C | – |
| Issue clarifications to client | C | C | A/R | C | I | – |
| Estimate approval (G2) | R | A | C | C | I | C |
| Assumptions, exclusions, qualifications | R | C | R | C | A | – |
| Risk allowances and margin (G3) | C | C | R | C | A | – |
| Final bid submission (G4) | I | C | R | C | A | – |
| Knowledge corpus updates | – | – | I | A | – | – |

The System Administrator is Responsible for applying knowledge corpus updates once the Design Manager approves them.

## 4. Target Fire Protection Systems and Phasing

| System | Takeoff scope | Indicative reference | Phase |
|---|---|---|---|
| Automatic sprinkler: wet pipe | Sprinklers, pipework (mains, branches, risers, drops), fittings, installation control valve sets, subsidiary / zone control valves, flow and tamper switches, test and drain | SS CP 52; Fire Code; spec | P1 |
| Sprinkler: dry pipe, pre-action, deluge | As above, plus valve sets, air compressors and detection interfaces | SS CP 52; spec (NFPA/FM where specified) | P2 |
| Fire pump systems | Duty/standby pumps, jockey pumps, controllers, pump-room pipework, test headers / flow test arrangements | SS CP 52; Fire Code; spec | P2 |
| Rising mains (dry / wet) | Risers, landing valves, breeching inlets, associated pipework | SS 575; Fire Code | P2 |
| Fire hydrant system | Hydrants, underground and above-ground pipework, breeching inlets | SS 575; Fire Code | P2 |
| Hose reel system | Hose reels, hose reel pumps where applicable, pipework | SS 575; Fire Code | P2 |
| Fire water storage | Tanks and associated connections | Fire Code; spec | P2 |
| Supports and ancillaries | Hangers, supports, sleeves, pipe painting and identification | Spec | P2 (sleeves P3) |
| Seismic restraint | Only where the project specification requires it | Project spec only | P3 (Could) |

Pipe materials and joining methods in scope: black and galvanised steel; grooved, threaded, welded and flanged joints; other materials (e.g. CPVC) where specified.

## 5. To-Be Business Process and Approval Gates

This replaces the v1 linear workflow (v1 §18). It adds a bid/no-bid decision, named human gates, an addenda loop and post-tender feedback.

| Stage | Activity | Primary agents | Human gate / output |
|---|---|---|---|
| S0 | Tender receipt; bid/no-bid qualification | Bid Orchestrator | G0 Bid/no-bid (Commercial Director) |
| S1 | Ingestion, classification, drawing and spec registers, revision control | Document Intelligence | Register confirmed (Estimator) |
| S2 | Drawing understanding and quantity takeoff | Drawing Understanding, QTO | G1 QTO verified (Senior Estimator) |
| S3 | Specification analysis and compliance cross-check | Specification, Compliance Knowledge | Findings reviewed (Design Manager) |
| S4 | Multi-discipline coordination (Phase 3) | Coordination | Issues triaged (Design Manager) |
| S5 | Tender clarifications, issued before the clarification cut-off | Clarification & RFI | Issued (Bid Manager) |
| S6 | BOQ, pricing and labour | BOQ, Pricing, Labour | G2 Estimate approved (Senior Estimator) |
| S7 | Risk, assumptions, exclusions and commercial terms | Bid Risk | G3 Commercial approval (Commercial Director) |
| S8 | Tender package assembly and submission | Package Assembly | G4 Submission sign-off (Commercial Director); package frozen |
| S9 | Post-tender: outcome capture; if awarded, handover and actuals feedback | Bid Orchestrator | Outcome recorded (Bid Manager) |

**Addenda loop.** An addendum or clarification response can arrive at any stage. When it does, the platform identifies the affected sheets, clauses and BOQ lines, runs a delta analysis and reopens only the affected stages. Gates already passed are re-confirmed only for the affected items.

**Deadline control.** The clarification cut-off and submission deadline drive all task due dates. The Bid Orchestrator warns owners when a stage is at risk of missing a deadline.

## 6. Functional Requirements

Each requirement has a unique ID, a MoSCoW priority (**M** Must, **S** Should, **C** Could) and a target phase (P1–P4, see §13). Numeric thresholds in the acceptance criteria are proposals and will be confirmed after the Phase 0 baseline. Unless stated otherwise, accuracy is measured against the golden dataset.

### 6.1 Bid Workspace and Orchestration (FR-BID)

| ID | Requirement | Priority | Phase |
|---|---|---|---|
| FR-BID-01 | Create a bid workspace per tender holding the client (main contractor, developer or consultant), project, tender reference, submission deadline, clarification cut-off, tender validity period and bid team. Acceptance: A bid cannot leave "Registered" until the mandatory fields are complete. Deadlines show on the bid dashboard. | M | P1 |
| FR-BID-02 | Track each bid's stage, gate status, open tasks and blockers against the process in §5. Acceptance: The dashboard shows the current stage, gate status and overdue tasks for every active bid. | M | P1 |
| FR-BID-03 | Send deadline alerts to task owners at configurable intervals before the clarification cut-off and the submission deadline. | S | P1 |
| FR-BID-04 | Support one project with several bids (e.g. to different main contractors). The bids share one verified QTO; each keeps its own documents, commercial terms and pricing. Acceptance: Users of one bid cannot see another bid's client-specific documents or prices unless they are members of both. | S | P2 |
| FR-BID-05 | Capture the bid/no-bid qualification: project type, value band, systems required, estimating capacity, key risks and client history. Record the decision and the approver. Acceptance: The decision and approver are logged before estimating tasks are assigned. | S | P4 |
| FR-BID-06 | Orchestrate the agents end to end across stages S1–S8 (full Bid Orchestrator). | S | P4 |

### 6.2 Tender Document Intelligence (FR-DOC)

| ID | Requirement | Priority | Phase |
|---|---|---|---|
| FR-DOC-01 | Ingest PDF (vector and scanned), DWG/DXF, XLSX/CSV and DOCX. From Phase 3, also ingest RVT, IFC and NWD/NWC. Acceptance: Every file is stored with a checksum. Unsupported or corrupt files are reported to the user, never silently dropped. | M | P1 |
| FR-DOC-02 | Classify each file and sheet by discipline, document type (drawing, specification, BOQ, schedule, addendum, clarification response, contract conditions), drawing number, title, revision, level, zone and scale. Acceptance: ≥95% correct drawing number and revision on vector title blocks. Low-confidence classifications go to a review queue. | M | P1 |
| FR-DOC-03 | Maintain a drawing register and a specification register with exactly one "Current" revision per sheet or document. | M | P1 |
| FR-DOC-04 | Detect superseded revisions and exclude them from QTO and pricing. Conflicting revision information (e.g. title block vs transmittal) is flagged for a person to resolve. Acceptance: Seeded superseded sheets are excluded in 100% of regression tests. | M | P1 |
| FR-DOC-05 | Register addenda and clarification responses. Link each change to the affected sheets, specification clauses and BOQ lines. | M | P1 |
| FR-DOC-06 | Assess input quality per sheet (vector or raster, resolution, legibility, scale present) and show the user the expected accuracy band. Acceptance: Raster sheets below the quality threshold are flagged "manual takeoff recommended". | M | P1 |
| FR-DOC-07 | Keep lineage for every extracted item: source file, sheet, revision, page and region. | M | P1 |
| FR-DOC-08 | Compare revisions of a sheet visually and geometrically, highlighting fire protection elements that were added, removed or changed. | S | P2 |

### 6.3 Drawing Understanding: Vision and CAD (FR-VIS)

| ID | Requirement | Priority | Phase |
|---|---|---|---|
| FR-VIS-01 | Use deterministic extraction first (DWG/DXF entities, blocks and layers; PDF vector paths). Use raster vision only where deterministic data is unavailable or insufficient. Acceptance: The extraction method is recorded for every detected object. | M | P1 |
| FR-VIS-02 | Read each drawing legend and map consultant-specific symbols to a canonical fire protection object library. Unmapped symbols are raised for the user to map. Mappings are reusable per consultant. Acceptance: No object of an unmapped symbol type is counted silently. | M | P1 |
| FR-VIS-03 | Detect sprinklers (type and orientation where shown), pipework (mains, branch lines, risers, drops) and valves. Acceptance: Meets the P1 accuracy KPIs in §14. | M | P1 |
| FR-VIS-04 | Detect fire protection equipment: pumps, tanks, breeching inlets, hydrants, hose reels, landing valves and test headers. | S | P2 |
| FR-VIS-05 | Determine and verify drawing scale from the stated scale and a known dimension; let users calibrate. Block length measurement on sheets marked NTS or with an unverified or conflicting scale. Acceptance: No lengths are produced from NTS or unverified-scale sheets. | M | P1 |
| FR-VIS-06 | OCR annotations (pipe sizes, tags, notes) and link each to the relevant object, with a confidence score. | M | P1 |
| FR-VIS-07 | Record level, zone, grid reference and sheet coordinates for every object. | M | P1 |
| FR-VIS-08 | Recognise enlarged plans, key plans, sections, riser schematics and details that repeat elements shown elsewhere, as input to de-duplication. | M | P1 |
| FR-VIS-09 | Give every detection a calibrated confidence score. In Phase 1 no detection is auto-accepted; all go to verification. Acceptance: Confidence is calibrated: items scored 0.9 are correct about 90% of the time on the golden set. | M | P1 |

### 6.4 Quantity Takeoff (FR-QTO)

| ID | Requirement | Priority | Phase |
|---|---|---|---|
| FR-QTO-01 | Take off sprinklers by type (pendent, upright, sidewall, concealed), K-factor, temperature rating, response type and finish, where stated in drawings or specification. Otherwise mark the attribute "not specified". | M | P1 |
| FR-QTO-02 | Take off pipework in metres by diameter, material, schedule/class, joining method and classification (main, branch, riser, drop). | M | P1 |
| FR-QTO-03 | Calculate vertical and hidden quantities (risers from riser schematics and floor-to-floor heights; sprinkler drops and arm-overs from ceiling and soffit heights) using configurable rules. Label them "rule-derived" and show the rule. Acceptance: Every rule-derived item shows the rule, its inputs and their source. | M | P1 |
| FR-QTO-04 | Take off drawn fittings (elbows, tees, reducers, couplings, flanges, grooved fittings). Where fittings are not drawn, derive them by rule or configurable allowance, labelled as such. | M | P1 |
| FR-QTO-05 | Take off valves and assemblies: installation control valve sets, subsidiary / zone control valves, gate or butterfly valves, check valves, test and drain assemblies, pressure-reducing valves, and flow and tamper switches. | M | P1 |
| FR-QTO-06 | Take off equipment: duty/standby fire pumps, jockey pumps, controllers, tanks, breeching inlets, test headers, hydrants, hose reels and landing valves. | S | P2 |
| FR-QTO-07 | Derive hangers and supports by rule (spacing by pipe size per specification). Include seismic restraint only where the project specifies it. | S | P2 |
| FR-QTO-08 | Prevent double counting across sheets and views (enlarged plans, overlapping match-lines, sections, schematics). Show each suspected duplicate with both evidence locations for resolution. Acceptance: ≥95% of seeded duplicates detected. Zero unresolved duplicates at G1. | M | P1 |
| FR-QTO-09 | Create an evidence record for every QTO item with the fields in Appendix B. Acceptance: 100% of QTO items have all mandatory fields. | M | P1 |
| FR-QTO-10 | Keep net measured quantity separate from wastage and allowance factors, and show both. | M | P1 |
| FR-QTO-11 | Let estimators add, edit or delete items with on-drawing measurement tools. Manual items are tagged "manual" with user and timestamp. | M | P1 |
| FR-QTO-12 | Produce a delta QTO on a revision or addendum: re-process affected sheets only, show quantity changes against the baseline, and keep prior verifications for unchanged elements. Acceptance: Unchanged, previously verified items keep their verified status after re-processing. | S | P2 |

### 6.5 Verification Workbench (FR-REV), new in v2

Human verification is the platform's main quality control. The workbench is where estimators spend most of their time, so it is a Phase 1 Must.

| ID | Requirement | Priority | Phase |
|---|---|---|---|
| FR-REV-01 | Overlay detected items on the source drawing, colour-coded by status and confidence. Navigate from any QTO or BOQ line to its drawing location and back. Acceptance: From any BOQ line, the user reaches the evidence on the drawing in ≤2 clicks. | M | P1 |
| FR-REV-02 | Provide a review queue ordered by risk (low confidence × cost impact), filterable by sheet, system, level and item type. | M | P1 |
| FR-REV-03 | Allow accept, edit and reject on single items and in bulk. Log every action with user, time, before and after values and a reason code. | M | P1 |
| FR-REV-04 | Show verification coverage (% of items and % of value verified). G1 cannot pass until the configured coverage policy is met. Acceptance: In Phase 1 the default policy is 100% of items verified. | M | P1 |
| FR-REV-05 | Support a sampling mode for categories with proven accuracy: review a configurable statistical sample, and send the whole category back to full review if sample error exceeds the threshold. | S | P2 |
| FR-REV-06 | Capture corrections as labelled evaluation and training data, subject to the data-usage policy (§11.4). | S | P1 |

### 6.6 BOQ Generation and Reconciliation (FR-BOQ)

| ID | Requirement | Priority | Phase |
|---|---|---|---|
| FR-BOQ-01 | Generate a BOQ from verified QTO using a configurable company structure (system → level/zone → item). | M | P1 |
| FR-BOQ-02 | Import a client-issued BOQ or pricing schedule (Excel) and map QTO items to its lines. Acceptance: ≥90% of mappings suggested correctly on the golden set. Unmapped lines are listed. | M | P1 |
| FR-BOQ-03 | Report variances between client BOQ quantities and measured QTO above configurable thresholds, and list items measured but missing from the client BOQ. Variances feed clarifications and qualifications. Acceptance: Every variance above threshold appears in the reconciliation report with evidence. | M | P1 |
| FR-BOQ-04 | Export priced or unpriced BOQs in the client's original Excel format (structure preserved) and in the company format. Acceptance: A re-imported export matches the client's original structure line for line. | M | P1 |
| FR-BOQ-05 | Trace every BOQ line to QTO items and evidence. Lines with no trace (provisional sums, lump sums) must be explicitly marked as such. Acceptance: Automated check blocks G1 and G2 if any unmarked line has no trace. | M | P1 |
| FR-BOQ-06 | Configure the measurement convention per tender (e.g. pipe measured net along centreline; fittings enumerated or deemed included) and state it in the tender qualifications. | S | P1 |

### 6.7 Specification Analysis (FR-SPEC)

| ID | Requirement | Priority | Phase |
|---|---|---|---|
| FR-SPEC-01 | Extract the specification attributes needed to classify QTO items: pipe material, schedule/class, joining method and sprinkler types. | M | P1 |
| FR-SPEC-02 | Extract the full scope and obligations: testing (hydrostatic), flushing, painting, identification, commissioning, approved makes / AVL, warranty, defects liability and maintenance period, spares, training, submittals. | M | P2 |
| FR-SPEC-03 | Cross-check drawings against the specification and flag conflicting, missing and ambiguous items, citing both sources. Acceptance: Every flag cites document, clause or page, and revision for both sides. | M | P2 |
| FR-SPEC-04 | Generate a scope and interface matrix per system (e.g. power supply to pumps, water supply connection, builder's works, ceiling openings, painting), each entry linked to its clause. | S | P2 |
| FR-SPEC-05 | Cite the document, clause or page, and revision for every extracted requirement. | M | P1 |

### 6.8 Compliance Knowledge (FR-CMP)

| ID | Requirement | Priority | Phase |
|---|---|---|---|
| FR-CMP-01 | Maintain the version-pinned knowledge corpus described in §2.2. Record edition, effective date, source, owner and licence status for each document. | M | P3 |
| FR-CMP-02 | Label every compliance statement with one source type (§2.3) and cite clause and edition. A statement with no retrieved citation must be labelled "AI recommendation". Acceptance: Zero uncited statements labelled as requirements in the evaluation set. Citation accuracy ≥98%. | M | P3 |
| FR-CMP-03 | Let users select the code edition that applies to each project. Keep superseded editions available. | M | P3 |
| FR-CMP-04 | Treat compliance findings as advisory. A regulatory interpretation with material consequence requires Design Manager approval, and QP review where applicable, before use. | M | P3 |
| FR-CMP-05 | Flag proposed products that need regulated fire-safety product certification (e.g. a Certificate of Conformity under SCDF's product listing requirements) or AVL approval, and flag missing evidence. | S | P3 |

### 6.9 Multi-Discipline Coordination (FR-CRD)

| ID | Requirement | Priority | Phase |
|---|---|---|---|
| FR-CRD-01 | Ingest IFC, Revit and Navisworks models and 2D multi-discipline drawings (architectural, structural, ACMV, electrical, plumbing). | M | P3 |
| FR-CRD-02 | Detect hard clashes and clearance issues from deterministic geometry. A clash raised from vision alone requires explicit drawing evidence. Acceptance: Every issue references the geometry or drawing evidence it came from. | M | P3 |
| FR-CRD-03 | Detect beam, duct and cable-tray conflicts, ceiling void congestion, pipe crossings, sleeve and opening needs, valve access and maintenance clearance, and pump-room / plant-room layout issues. | M | P3 |
| FR-CRD-04 | Give each issue a severity, evidence (views, snapshots, coordinates), affected items and a suggested owner. Group duplicate issues. | M | P3 |
| FR-CRD-05 | Let users mark false positives, and use that feedback to tune rules. Acceptance: Coordination precision meets the §14 target. | S | P3 |
| FR-CRD-06 | Align object classification with IFC-SG / CORENET X conventions where project models use them. | C | P4 |

### 6.10 Tender Clarifications and RFIs (FR-RFI)

| ID | Requirement | Priority | Phase |
|---|---|---|---|
| FR-RFI-01 | Separate tender clarifications (before award, sent to the client before the clarification cut-off) from construction RFIs (after award; out of scope, see §1.4). | M | P2 |
| FR-RFI-02 | Draft clarifications from flagged issues. Each draft has: number, subject, project, level/grid, sheet and revision, problem description, evidence, options, potential cost and programme impact, and required reviewer. Acceptance: Every draft has at least one evidence reference. | M | P2 |
| FR-RFI-03 | Draft clarifications from coordination issues. | M | P3 |
| FR-RFI-04 | Keep a clarification register with the lifecycle in §7 and due dates set from the clarification cut-off. Link responses back and assess their impact (delta QTO and pricing). | M | P2 |
| FR-RFI-05 | At submission, propose any unresolved clarification as a tender qualification or assumption for review. | M | P2 |
| FR-RFI-06 | Treat options as recommendations only. Options with engineering, fire-safety or structural content need Design Manager approval (and QP input where applicable) before issue. | M | P2 |
| FR-RFI-07 | Group related issues into one clarification, and output in the client's template where one is provided. | S | P2 |

### 6.11 Costing and Pricing (FR-CST)

| ID | Requirement | Priority | Phase |
|---|---|---|---|
| FR-CST-01 | Maintain a company rate library of unit rates by item, size, material and brand, each with source, date and validity. Apply it to BOQ lines. | S | P1 |
| FR-CST-02 | Capture supplier quotations from PDF, Excel and email. Extract supplier, items, unit price, currency, quote date, validity, lead time, MOQ and delivery terms; a person confirms the extraction. | M | P2 |
| FR-CST-03 | Record provenance for every price (source record, timestamp, validity). Flag expired quotes and quotes whose validity is shorter than the tender validity period. Acceptance: No BOQ line is priced without a source record. | M | P2 |
| FR-CST-04 | Price in SGD, with foreign-currency quotes converted at a recorded FX rate (with source and date), plus freight, insurance, import charges and a configurable FX buffer. | M | P2 |
| FR-CST-05 | Show prices exclusive of GST, with GST calculated separately at the configurable prevailing rate. | M | P2 |
| FR-CST-06 | Build up cost as separate, visible lines: materials, fittings, valves, equipment, labour, supervision, access equipment, testing and commissioning, transport, subcontract, wastage, site overheads, preliminaries, insurance, bonds, contingency and margin. | M | P2 |
| FR-CST-07 | Compare current prices with historical PO and project rates, and flag outliers beyond a configurable tolerance. | S | P2 |
| FR-CST-08 | Integrate with the company ERP for item master, purchase orders and historical costs (system to be confirmed, decision D4). | S | P2 |
| FR-CST-09 | Never generate or estimate a supplier price. Where no sourced price exists, the line shows "unpriced" or an allowance clearly labelled as the estimator's. | M | P2 |

### 6.12 Labour Estimation (FR-LAB)

| ID | Requirement | Priority | Phase |
|---|---|---|---|
| FR-LAB-01 | Maintain a baseline productivity library (e.g. man-hours per metre by pipe size and joining method, per sprinkler, per valve assembly), each entry with its source: company standard, historical project or estimator judgement. | M | P2 |
| FR-LAB-02 | Apply adjustment multipliers for installation height, access, MEP congestion and work environment (basement, occupied or live building, night work, high-rise logistics). Show each multiplier with its source and rationale, separate from the baseline. Acceptance: The labour line shows baseline hours and each multiplier separately. | M | P2 |
| FR-LAB-03 | Build up labour rates per trade and grade from company rate tables: wages, foreign worker levy, accommodation, transport, insurance (e.g. WICA), overtime and supervision. | M | P2 |
| FR-LAB-04 | Output crew-days and a manpower histogram to sanity-check the programme. | C | P3 |
| FR-LAB-05 | Learn productivity from actual project labour hours when available. Changes to the baseline library need estimator approval. | S | P4 |

### 6.13 Bid Risk and Commercial Review (FR-RSK)

| ID | Requirement | Priority | Phase |
|---|---|---|---|
| FR-RSK-01 | Check scope against a per-system checklist: pumps, tanks, breeching inlets, hydrants, hose reels, hydraulic calculations, shop drawings, testing and commissioning, authority inspections and FSC support, builder's works, and power supply interfaces. Acceptance: Every checklist item is resolved as included, excluded, by others, or clarified. | M | P2 |
| FR-RSK-02 | Identify design-responsibility risk: whether the contractor carries design and build, shop drawing, hydraulic calculation or QP engagement obligations. | M | P2 |
| FR-RSK-03 | Flag execution risks with evidence: access, night work, shutdowns, high-rise, basement, live or occupied buildings, congested ceilings, work at height. | M | P2 |
| FR-RSK-04 | Quantify or flag the potential cost and programme impact of each risk (range or allowance), subject to estimator acceptance. | M | P2 |
| FR-RSK-05 | Draft assumptions, exclusions, qualifications and deviations, each linked to its source issue. | M | P2 |
| FR-RSK-06 | Keep a bid risk register with owner, treatment (price, qualify, clarify, accept) and status. | S | P2 |
| FR-RSK-07 | Extract contract terms with clause citations (liquidated damages, retention, performance bond, payment terms, defects liability, design liability, back-to-back terms with the main contractor, price fluctuation, insurance) and compare them against company risk-appetite thresholds. | S | P4 |

### 6.14 Tender Package (FR-PKG)

| ID | Requirement | Priority | Phase |
|---|---|---|---|
| FR-PKG-01 | Generate an internal review pack for G2 and G3: estimate summary, key cost drivers, margin, risk allowances, top variances and open issues. | M | P2 |
| FR-PKG-02 | Require a recorded approval by an authorised approver before submission. The platform cannot send a bid externally without an explicit named-user action. Acceptance: Role-based access tests confirm no external transmission path without G4 approval. | M | P2 |
| FR-PKG-03 | Freeze the submitted package as an immutable snapshot, including all underlying QTO, prices, evidence and approvals. | M | P2 |
| FR-PKG-04 | Assemble the full submission: priced BOQ (client format), cost summary, qualifications, assumptions and exclusions, clarification log, deviations list, technical submittal schedule and company forms. | S | P4 |

### 6.15 Learning and Feedback (FR-LRN)

| ID | Requirement | Priority | Phase |
|---|---|---|---|
| FR-LRN-01 | Release model and prompt changes through governed retraining, with regression tests against the golden dataset before release. Acceptance: No model version reaches production without passing the regression suite. | M | P1 |
| FR-LRN-02 | Capture the tender outcome: awarded or lost, awarded price if known, reasons and competitor feedback. | S | P2 |
| FR-LRN-03 | Change the rate and productivity libraries only through estimator-approved updates. No learning process may change a library silently. | M | P2 |
| FR-LRN-04 | Link post-award purchased quantities, actual costs and labour hours to the tender estimate for variance analysis. | S | P4 |

### 6.16 Administration and Libraries (FR-ADM)

| ID | Requirement | Priority | Phase |
|---|---|---|---|
| FR-ADM-01 | Role-based access control using the roles in §3, with membership set per bid. | M | P1 |
| FR-ADM-02 | Version the canonical object library, consultant symbol mappings, measurement rules and allowance factors, with change history. | M | P1 |
| FR-ADM-03 | Configure company BOQ templates and client templates. | M | P1 |
| FR-ADM-04 | Provide an audit log viewer with export. | M | P1 |
| FR-ADM-05 | Monitor usage and AI processing cost per bid, with budget alerts. | S | P1 |

### 6.17 Design Development and Folder Intake (FR-DSN, FR-DOC-09/10), added 2 Oct 2026

<!-- Proposed on 2026-10-01 from the MOH (TTSH) design-intent tender and approved by the product owner on 2026-10-02 (ADR-011). This section is NOT yet in the .docx this file is generated from: carry it into the .docx before regenerating, or it will be lost. -->

A design-intent tender draws the mains and leaves the heads and range pipes to the contractor ("the contractor shall be responsible for the further development and detailed design"). A takeoff of what is drawn counts no heads, so most of the sprinkler cost is missed. These requirements let the platform propose that development as an estimating aid. A proposed layout is never a design for the Qualified Person's approval or for construction (§2.1).

| ID | Requirement | Priority | Phase |
|---|---|---|---|
| FR-DSN-01 | Recognise a design-intent sheet from its notes, and read the design criteria its notes state (maximum spacing, area per head, K-factor) as proposals, each citing the sheet and the words it was read from. No layout is made until a Senior Estimator or Design Manager confirms the criterion for the sheet. Acceptance: A sheet with heads already drawn is flagged, so a layout is not counted on top of them. A sheet with no plan view at a verified or calibrated scale is refused, with the reason. | M | P1 |
| FR-DSN-02 | Find the spaces of a plan from the architect's base linework (rooms through their doorways, open floors, the building's outline), with each space's name and area. Lift shafts, risers, voids and stairs are left without heads, and every omission is listed with the rule that omitted it. Acceptance: On the synthetic plan, each room's area is within 5% of its drawn area, and nothing outside the building is a space. | M | P1 |
| FR-DSN-03 | Propose sprinkler heads and range pipes for each space from a versioned set of design rules (preferred grid, omissions, head type and rating by space and level, range-pipe sizing table), never exceeding the confirmed criterion. Range pipes are sized by the heads each length feeds and fed from the nearest drawn pipe; a row with no drawn pipe in reach is given an allowance and says so. Acceptance: No head covers more than the criterion's area. The same sheet, criterion and rule version give the same proposal. | M | P1 |
| FR-DSN-04 | Take proposed heads, drops and range pipe into the QTO as rule-derived items of their own, marked "proposed layout, not drawn", never added into what was drawn. Each carries the design rule, its version, the criterion and its source in its evidence record, and goes through the same verification as any other proposal. Acceptance: What was drawn is counted exactly as before. A person's rejection of a proposed head survives a new layout. | M | P1 |
| FR-DSN-05 | Export a sheet's proposed layout over its tender drawing as PDF or DXF, stamped "For estimation only: not for construction". | S | P2 |
| FR-DSN-06 | Limit each sheet's layout to its own side of its match lines, so floors shared between sheets are designed once. | S | P2 |
| FR-DOC-09 | Accept a whole folder as one upload, keeping each file's path within it, with files of any size the store allows and `.rar` archives opened as `.zip` archives are. Acceptance: Every file in the folder is either stored or reported with the reason, and a file already in the bid is not stored twice. | M | P1 |
| FR-DOC-10 | Give each uploaded document an origin: tender document (client-issued), our working document, reference, or ignored. The platform proposes it from the file's folder and name and a person confirms it. Only tender documents feed the registers, revision control, takeoff and BOQ mapping. Acceptance: A contractor's marked-up copy of a tender drawing never becomes the Current revision of that drawing. | M | P1 |

## 7. State Models

v1 used a single approval state machine. It mixed the contractor's own workflow with external design and SCDF approvals the contractor does not own. v2 defines five separate state models. Transitions are recorded in the audit log with user, time and reason.

| Model | States (in order) | Key rules |
|---|---|---|
| Bid lifecycle | Registered → Qualifying → In preparation → Under review (G1–G3) → Approved for submission → Submitted → Post-submission clarification → Awarded \| Lost \| Withdrawn \| No-bid | Only the G4 approver can move a bid to Submitted. A Submitted bid is frozen (FR-PKG-03). |
| Document / sheet revision | Received → Registered → Current \| Superseded \| Withdrawn \| Conflict | Only Current sheets feed QTO. Conflict blocks use until a person resolves it. |
| QTO item | Detected → Proposed → Verified \| Edited \| Rejected → Baselined (at G2) → Superseded (on revision or addendum) | Baselined items are immutable. Changes create new proposals. Superseded items need re-verification only if the element changed. |
| Clarification | Draft → Internal review → Approved to issue → Issued → Responded → Closed: incorporated \| Closed: no change \| Converted to qualification | Engineering options need Design Manager approval before "Approved to issue". Unresolved items at G4 are converted to qualifications. |
| External design / regulatory approval (future, tracking only) | Draft → Internal contractor review → Consultant / QP review → SCDF submission (by QP) → Approved \| Conditionally approved \| Revision required → Approved for construction | Keeps the v1 states. Status is entered by authorised people; the platform never submits. A new revision touching an approved element raises an "approval impact" flag to the Design Manager. |

## 8. Agent Architecture

### 8.1 Principles

- **Orchestrated, not a swarm.** A durable workflow graph decides which agent runs when. Agents cannot call each other or take actions outside the graph.
- **Deterministic first.** Geometry, measurement, unit conversion and pricing arithmetic run in deterministic code. AI models interpret, classify and draft; they never compute final quantities or prices in free text.
- **Evidence-bound.** Every output follows a schema and references its evidence and confidence.
- **Proposals, not commitments.** Agent outputs stay proposals until the relevant human gate approves them.
- **Observable.** Every run is traced: inputs, tool calls, model and prompt versions, outputs, latency and cost.

### 8.2 Autonomy Levels

| Level | Name | Description | Permitted for |
|---|---|---|---|
| L0 | Inform | Analyses and flags only. | All agents |
| L1 | Draft | Produces drafts that a person must approve before use. | QTO items, BOQ mappings, clarifications, compliance findings, risks, assumptions, prices |
| L2 | Bounded action | Takes reversible, pre-approved routine actions that are logged and can be undone. | Classify documents, set a revision to Superseded when unambiguous, re-process addenda, create tasks, send internal reminders |
| L3 | Commit | Takes irreversible or external commitments. | Not permitted. Covers authority submissions, sending clarifications or bids externally, contractual commitments, and changes to approved design information. |

### 8.3 Agent Catalogue

This replaces the v1 agent swarm table (v1 §17). It adds the agents v1 described but left out of that table, and splits combined agents where responsibilities or approvers differ.

| Agent (v1 name) | Responsibility and key outputs | Max level | Human gate | Phase |
|---|---|---|---|---|
| Bid Orchestrator (Bid Manager Agent) | Runs the workflow graph, assigns tasks, tracks deadlines and gates. Outputs bid state, tasks and gate status. | L2 | All gates | P1 basic; P4 full |
| Document Intelligence (Document Agent) | Ingestion, classification, registers, revision control, addenda linkage, lineage. | L2 | Register confirmation | P1 |
| Drawing Understanding (Fire Drawing Vision Agent) | CAD/vector parsing, vision, OCR, legend mapping, scale. Outputs objects with geometry and coordinates. | L1 | Workbench | P1 |
| QTO Agent | Measurement, rule-derived quantities, de-duplication, evidence records, delta QTO. | L1 | G1 | P1 |
| BOQ Agent (new) | Builds the BOQ, maps and reconciles the client BOQ, exports. | L1 | G1 / G2 | P1 |
| Specification Agent (v1 §9, missing from table) | Extracts requirements, finds drawing-vs-spec conflicts, builds the scope matrix. | L1 | Design Manager | P1 limited; P2 |
| Compliance Knowledge (Singapore Compliance Agent) | Cited retrieval from the pinned corpus, with source-type labels. | L1 | Design Manager (+QP) | P3 |
| Coordination Agent | Multi-discipline issue detection with geometry evidence. | L1 | Issue triage | P3 |
| Clarification & RFI Agent | Drafts clarifications, keeps the register, converts unresolved items to qualifications. | L1 | Bid Manager (+Design Mgr) | P2 |
| Pricing Agent (split from Cost & Labour) | Quote extraction, rate lookup, provenance, FX and GST, cost build-up. | L1 | G2 | P1 rates; P2 |
| Labour Agent (split from Cost & Labour) | Productivity × multipliers; labour rate build-up. | L1 | G2 | P2 |
| Bid Risk Agent (v1 §16, missing from table) | Scope gaps, execution risks, contract terms, assumptions and exclusions. | L1 | G3 | P2; P4 contract |
| Package Assembly Agent (new) | Review pack, submission package, frozen snapshot. | L1 | G3 / G4 | P2; P4 full |

### 8.4 Agent Contract

Every agent must meet the following requirements:

- Use typed input and output schemas, and validate outputs before saving them.
- Attach evidence references and a confidence score to every assertion.
- Run idempotently and be re-runnable. Record model, prompt, tool and library versions on every run.
- On failure or low confidence, time out, retry within limits, then escalate to a human task. Never fall back silently to a guess.
- Respect a cost and time budget per run. Exceeding it pauses the run and alerts the owner.
- Never write to approved or baselined data. A change creates a new proposal.
- Have an evaluation suite that runs on every model or prompt change (regression against the golden dataset).

## 9. Guardrails and Control Matrix

This turns the v1 guardrails into controls that can be tested. Items 12 and 13 are new.

| # | Guardrail | Control mechanism | Verification |
|---|---|---|---|
| 1 | No hallucinated quantities | Quantities come only from geometry, OCR'd values or declared rules, each linked to evidence. The BOQ rejects lines with no trace unless marked provisional or lump sum. | Automated check at G1/G2: 100% of BOQ lines traceable |
| 2 | No hallucinated code requirements | A compliance statement needs a retrieved clause citation from the pinned corpus. Uncited statements are labelled "AI recommendation". | Citation accuracy ≥98%; zero mislabelled requirements in the evaluation set |
| 3 | No obsolete revisions | Only Current sheets feed QTO. Revision conflicts block use until resolved. | Seeded superseded sheets excluded in 100% of regression tests |
| 4 | No duplicate counting | De-duplication across sheets and views. Unresolved duplicates block G1. | ≥95% of seeded duplicates detected; zero unresolved at G1 |
| 5 | No unsupported RFI recommendations | Each clarification needs at least one evidence reference. Engineering options need Design Manager approval. | Automated check; approval log |
| 6 | No invented supplier prices | Price fields accept only values linked to a source record (quote, PO, rate library) with date and validity. | Zero unsourced prices at G2 |
| 7 | No autonomous regulatory approval | No integration can submit to an authority. Approval states are set only by authorised people. | Architecture review; role-based access tests |
| 8 | No autonomous contractual commitment | Nothing is sent externally without a named user action. G4 approval is required for submission. | Role-based access tests; audit log |
| 9 | No silent change to approved information | Baselined and approved data is immutable. Changes create a new version with a diff and an approval. | Audit tests |
| 10 | Deterministic calculations | Measurement, unit conversion and pricing arithmetic run in deterministic code. | Code review; unit tests |
| 11 | Evidence for high-impact decisions | High-impact means: above a configurable value threshold, or regulatory, engineering or commercial-term content. These need evidence and a named approver. | Gate checks |
| 12 | Confidentiality (new) | Each bid and client is isolated. Third-party model providers may not train on or retain company data. | Security testing; vendor contract review |
| 13 | Automation-bias control (new) | Sampling audits even for high-confidence items, plus periodic blind re-measurement. | Sampling error metrics reported monthly |

## 10. Non-Functional Requirements

Targets are proposals and will be confirmed in Phase 0 against actual tender volumes and the company's IT policies.

| ID | Category | Requirement |
|---|---|---|
| NFR-01 | Performance | Ingest and classify a 300-sheet tender set within 1 hour. First-pass QTO for 50 fire protection sheets within 4 hours. Workbench interactions under 2 s (p95). |
| NFR-02 | Scalability | At least 10 concurrent active bids and 20 concurrent users without degradation. |
| NFR-03 | Availability | 99.5% during Singapore business hours. No planned maintenance within 48 hours of any active bid's submission deadline. |
| NFR-04 | Backup and DR | RPO ≤ 24 h; RTO ≤ 8 h. Frozen submission snapshots kept in immutable storage. |
| NFR-05 | Data residency | Production data and backups hosted in a Singapore cloud region. Any cross-border AI processing needs explicit approval and contractual zero-retention terms. |
| NFR-06 | Security | SSO with MFA; per-bid role-based access; TLS 1.2+ in transit and encryption at rest; secrets in a managed vault; annual penetration test; aligned to OWASP ASVS Level 2. |
| NFR-07 | Privacy | Comply with the PDPA for personal data in tender documents, supplier quotes and user records. |
| NFR-08 | Confidentiality | Treat tender documents as confidential third-party information. Isolate data per bid and client. Use data for model improvement only as the policy in §11.4 allows. |
| NFR-09 | Auditability | Keep an immutable audit log of AI outputs, evidence, human decisions and outcomes. Retention is configurable; default ≥7 years, aligned to company records policy and contractual limitation periods. |
| NFR-10 | Explainability | Any quantity, price or finding can be traced to its evidence and method in ≤2 clicks. |
| NFR-11 | Model governance | Register model and prompt versions. Every change passes regression on the golden dataset before release. Support rollback. Monitor drift in acceptance and correction rates. |
| NFR-12 | Usability | An estimator can verify a typical floor plate without leaving the workbench. Estimator onboarding training takes ≤1 day. English UI. Supports large-format drawings on dual monitors. |
| NFR-13 | Interoperability | XLSX import and export preserving client formats; CSV; PDF reports; IFC 4 (Phase 3). |
| NFR-14 | Observability | Trace every agent run (inputs, tool calls, tokens, latency, cost, errors), with dashboards and alerts. |
| NFR-15 | Cost control | Track AI processing cost per bid against a configurable budget. A target cost per tender is set in Phase 0. |

## 11. Data, Integration and Data Governance

### 11.1 Data Stores

- **Object storage:** drawings, BIM models, specifications, source documents and exports.
- **Relational database:** projects, bids, documents, revisions, QTO items, evidence, BOQ, rates, quotes, suppliers, clarifications, risks, approvals.
- **Vector store:** specifications, standards, project documents and historical knowledge, for cited retrieval.
- **Graph / semantic layer:** relationships between project, building, level, zone, drawing and element.
- **Event / workflow store:** agent runs, approvals, retries and audit events.
- **Immutable snapshot store (new):** frozen submitted packages (FR-PKG-03).

### 11.2 Core Data Entities

Project; Bid; Tender Package; Document; Sheet; Revision; Addendum; Detected Object; QTO Item; Evidence; Measurement Rule; BOQ Line; Client BOQ Mapping; Rate; Quotation; Supplier; Productivity Factor; Labour Rate; Clarification; Risk; Assumption / Qualification; Approval; Audit Event; Knowledge Document (with edition).

### 11.3 Integrations

| System | Purpose | Direction | Phase | Status |
|---|---|---|---|---|
| Company ERP / accounting | Item master, POs, historical costs | Inbound | P2 | System to be confirmed (D4) |
| Supplier quotations | Price capture from PDF, Excel or email | Inbound | P2 | Document ingestion; no supplier APIs assumed |
| Excel BOQ templates | Client BOQ import; priced export | Both | P1 | Confirmed need |
| DWG / Revit parsing | Geometry and object extraction | Inbound | P1 DWG; P3 Revit | SDK / licensing to be confirmed |
| IFC / IFC-SG | openBIM models | Inbound | P3 | – |
| Navisworks | Federated models, clash results | Inbound | P3 | – |
| Client common data environments | Download tender sets | Inbound | P4 (Could) | Manual upload until then |
| LLM providers | Model inference and embeddings through the AI gateway (§12.2): Anthropic Claude, OpenAI / Azure OpenAI, Google Gemini, self-hosted models | Both | P1 | Anthropic at launch; others enabled per ADR-004 and data-class approval (D2) |
| Identity provider | SSO and MFA | Inbound | P1 | e.g. Microsoft Entra ID; to be confirmed |
| Email / Teams | Internal notifications only | Outbound | P1 | – |

### 11.4 Data Ownership and AI Usage Policy

- Tender documents remain the property of the issuer. They are used only for the related bid and project.
- Learning across bids is limited to company-owned derived data (verified QTO patterns, symbol mappings, productivity, rates). Legal must confirm this against typical tender confidentiality terms (Q8).
- Third-party model providers must be contractually barred from training on or retaining company data (zero retention where available).
- Supplier prices are commercially sensitive and visible only to authorised roles.
- Retention and deletion rules are configurable per bid (e.g. lost bids archived after a set period, or purged where tender terms require).
- Singapore Standards and other licensed documents enter the knowledge corpus only where the licence allows it (dependency DP4).

## 12. Recommended Technology Pattern

§12.1 lists recommended components; architecture decision records in Phase 0 will confirm or replace each one. §12.2 records a confirmed decision on LLM providers.

### 12.1 Platform Components

- Python 3.12 / FastAPI for service and agent APIs, with synchronous database access.
- A PostgreSQL-backed job queue for background processing, with jobs queued in the same transaction as their data and no separate message broker.
- Agents as typed functions on the AI gateway (§12.2), run as queued jobs. The orchestration engine for Phase 4 (e.g. LangGraph or Temporal) is chosen at the Phase 3 exit.
- Vision-capable LLMs for drawing understanding, used through the AI gateway in §12.2.
- OCR plus deterministic CAD, BIM and PDF parsers (PDFium-based PDF extraction) for geometry and text, run in sandboxed workers, with malware scanning of every uploaded file.
- PostgreSQL 17 (or the newest version the managed service supports in Singapore) for transactional data, with row-level security on bid data and partitioned large tables; vector search (pgvector), with the embedding model recorded for every stored vector; object storage for source files.
- A React 19 web application. The drawing viewer uses deep-zoom tiles rendered on demand and a Canvas/WebGL overlay, so it stays responsive with tens of thousands of objects.
- Client workbooks written by patching only the target cells, so every other part of the client's file is preserved.
- Revit, IFC and Navisworks integrations for BIM coordination (Phase 3).
- ERP integration, file-based at first and API-based later.
- An evaluation harness and golden dataset, run in CI for every change to a model, prompt or provider configuration.
- Enterprise identity (Microsoft Entra ID), secrets management, logging, agent tracing (e.g. OpenTelemetry) and monitoring.
- A Linux Dev Container matching the production image, with automated dependency updates.

### 12.2 LLM Providers

Decision (22 September 2026): the platform supports multiple LLM providers, selected by configuration. ADR-004 decides which providers are enabled at launch and which data classes each may receive.

- **One gateway, many providers.** All model calls go through one AI gateway. Code names a task (a route), and configuration maps each route to a main model and ordered fallbacks across providers. Adding or switching a provider is a configuration change plus adapter tests, not an application change.
- **Candidate providers.** Anthropic Claude (direct, or through Amazon Bedrock, Google Vertex AI or Microsoft Foundry), OpenAI / Azure OpenAI, Google Gemini, and self-hosted open-weight models.
- **Approval by data class.** Each provider is approved for specific data classes: internal, confidential (tender documents, drawings, quantities), commercial (prices, quotations) and personal. Approval rests on the provider's region, retention and no-training terms. The gateway sends data only to approved providers, including on fallback; when none is available, the task goes to a person (NFR-05, NFR-08, §11.4). Providers hosted in Singapore or the region, with zero-retention terms where available, are preferred for confidential and commercial data.
- **Capability checks.** Each route declares the capabilities it needs (e.g. vision, document input, structured output). Configuration that assigns a route to a model without them is rejected at startup. Citations are checked against source text by deterministic code, whichever provider produced them.
- **Evidence-based model choice.** At launch every route uses Anthropic Claude Opus 5. A route's models change only when the golden-set evaluation compares candidates on quality, cost and latency, and the Product Owner approves (NFR-11).
- **Cost and traceability.** Every call records provider, model, prompt version and cost against the bid (FR-ADM-05, NFR-15).

## 13. Delivery Plan

### 13.1 MVP Definition (Phase 1)

This resolves the conflict between v1 §23 and §24. The MVP is the same thing as Phase 1.

| In the MVP | Deferred |
|---|---|
| Vector PDF and DWG/DXF ingestion. Scanned PDFs accepted with a quality warning. Drawing and specification registers, revision control, addenda register. Wet-pipe sprinkler systems: sprinklers, pipework (including rule-derived drops and risers), fittings, valves. Legend and symbol mapping; scale calibration. QTO with full evidence records and de-duplication. Verification workbench. BOQ generation, client BOQ mapping and reconciliation, Excel export. Should: unit rates from the company rate library. Basic bid workspace and deadlines, access control, audit trail. | Supplier quote ingestion and full cost build-up (P2). Labour productivity model (P2). Other fire protection systems (P2). Specification cross-checking beyond QTO attributes (P2). Clarification drafting and scope-gap checks (P2). Compliance knowledge agent and BIM coordination (P3). Contract-terms review, full tender package, bid/no-bid support (P4). |

**Rationale:  **Takeoff is the most effort-intensive and most measurable activity. The evidence and verification foundation built in Phase 1 is what every later agent relies on. Pricing and labour need data (quotes, productivity history) that Phase 0 and Phase 1 must first establish. Decision D1 confirms this scope.

### 13.2 Phased Roadmap

| Phase | Focus and major capabilities | Exit criteria (proposed) | Indicative duration |
|---|---|---|---|
| P0 Discovery and data readiness | Golden dataset (10–20 historical tenders with verified QTO and BOQ); current-state baseline; seed legend library; architecture and security approval; ERP confirmation; build vs buy assessment. | Baseline signed off; golden dataset ready; architecture decisions approved. | 6–8 weeks |
| P1 QTO Copilot (MVP) | See §13.1. | Sprinkler count ≥98% and pipe length within ±5% on the golden set; ≥30% QTO effort reduction in shadow pilot on ≥3 live tenders. | 4–6 months |
| P2 Estimating Copilot | Other fire protection systems; specification analysis; supplier quotes and cost build-up; labour; clarifications from spec and BOQ issues; scope-gap and execution risk; review pack; delta QTO; multi-bid projects. | Estimate turnaround −30%; zero unsourced prices; ≥70% of clarifications issued with only minor edits. | 4–6 months |
| P3 Coordination and Compliance | BIM/2D coordination; compliance knowledge agent; clarifications from coordination issues; product compliance. | Coordination precision ≥70%; citation accuracy ≥98%. | 4–6 months |
| P4 Agentic Bid Manager | End-to-end orchestration; contract-terms review; full tender package; bid/no-bid support; learning from actuals. | Bid cycle time −40%; missed-scope incidents down against baseline. | 4–6 months |

Durations are indicative and will be re-planned at the end of Phase 0.

### 13.3 Pilot and Rollout Approach

- **Shadow mode:** the AI runs alongside manual takeoff on live tenders and the results are compared. No AI quantities go into a submitted bid until the Phase 1 exit criteria are met.
- **Assisted mode:** AI quantities are used, with 100% human verification.
- **Sampling mode (P2+):** risk-based verification for item categories whose accuracy has been proven (FR-REV-05).
- **Change management:** estimator champions, one-day training, an in-app feedback channel and a monthly accuracy review.

### 13.4 Governance

- A steering committee (Sponsor, Commercial Director, Estimating Manager, Project Manager, Technical Lead) meets monthly.
- Phase-gate reviews are held against the exit criteria in §13.2.
- Changes to scope, targets or boundaries go through formal change control. A RAID log is maintained.

## 14. KPIs and Success Criteria

| KPI | Definition | Phase | Proposed target |
|---|---|---|---|
| Sprinkler count accuracy | 1 − \|AI − verified\| ÷ verified, per sheet (vector drawings) | P1 | ≥98% |
| Pipe length accuracy | \|AI − verified\| ÷ verified, by diameter, per sheet | P1 | ±5% (P1); ±3% (P3) |
| Missed-item rate | Verified items not detected ÷ verified items | P1 | ≤5% (P1); ≤2% at maturity |
| False-detection rate | Rejected AI items ÷ AI items | P1 | ≤5% |
| Duplicate detection | Duplicates flagged ÷ total duplicates (seeded and found) | P1 | ≥95%; zero unresolved at G1 |
| QTO effort | Estimator takeoff hours per tender vs baseline | P1 | −30% (P1); −60% at maturity |
| Tender turnaround | Working days from receipt to submission-ready | P2 | −30% vs baseline |
| Price provenance | Priced BOQ lines with a valid source ÷ priced lines | P2 | 100% |
| Clarification acceptance | AI drafts issued with only minor edits ÷ drafts | P2 | ≥70% |
| Coordination precision | Confirmed issues ÷ issues raised | P3 | ≥70% |
| Citation accuracy | Citations that correctly support their statement ÷ citations | P3 | ≥98% |
| Estimate variance | Tender estimate vs actual cost (awarded projects) | P4 | Within ±10% |
| Labour estimate accuracy | Estimated vs actual man-hours by activity | P4 | Within ±15% |
| Human escalation rate | Agent tasks escalated ÷ total agent tasks | P1+ | Monitor; trending down |
| Workflow and tool success | Agent runs completed without failure ÷ runs | P1+ | ≥98% |
| Business outcomes | Tenders per estimator per month; win rate; post-award missed-scope claims | P2+ | Monitor vs baseline |

## 15. Assumptions, Constraints and Dependencies

### 15.1 Assumptions

- **A1** Most tender drawings are vector PDF or DWG. Scanned-only sets are a minority (to be validated in Phase 0).
- **A2** The company can provide 10–20 historical tenders with verified QTO and BOQ.
- **A3** Tender documents are in English.
- **A4** Estimators remain accountable for quantities. The platform does not change how liability is allocated.
- **A5** Fire protection subcontractors rarely receive BIM models at tender stage, so BIM is deferred to Phase 3.
- **A6** Hosting in a Singapore cloud region is acceptable to the company and its clients.

### 15.2 Constraints

- **C1** The platform must not perform, or present itself as performing, professional engineering, QP or authority functions.
- **C2** Tender deadlines are fixed, so downtime near a deadline is business-critical.
- **C3** Tender confidentiality terms limit how data may be used.

### 15.3 Dependencies

- **DP1** Access to the ERP and historical cost data (system to be confirmed).
- **DP2** Estimator time for labelling and verification during Phases 0–1 (to be agreed with the Estimating Manager).
- **DP3** Licences for CAD and BIM parsing libraries.
- **DP4** Current, licensed copies of the SCDF Fire Code and relevant Singapore Standards, on licence terms that allow use in the knowledge corpus.

## 16. Risk Register

| ID | Risk | L | I | Mitigation | Owner |
|---|---|---|---|---|---|
| R1 | Variable drawing quality and formats reduce accuracy | H | H | Input-quality gate; CAD/vector first; manual fallback; report accuracy by input class | Tech Lead |
| R2 | Symbol conventions vary widely between consultants | H | M | Legend mapping library per consultant; reusable user mappings | Product Owner |
| R3 | Too little historical data for evaluation and learning | M | H | Phase 0 data readiness; start rule-based; collect labelled corrections from Phase 1 | Estimating Mgr |
| R4 | Automation bias: estimators rubber-stamp AI output | M | H | Coverage policy, sampling audits, blind re-measurement, clear accountability in RACI | Estimating Mgr |
| R5 | Liability for errors in submitted bids | M | H | Human gates, frozen snapshots, qualifications, review of PI insurance | Commercial Dir |
| R6 | Regulatory knowledge goes stale | M | H | Named corpus owner; edition pinning; monitoring of SCDF circulars | Design Mgr |
| R7 | Confidentiality breach or dispute over data use | L | H | Isolation, zero-retention AI terms, data policy approved by legal | Data Steward |
| R8 | Scope creep into design automation | M | M | Out-of-scope list, change control, operating principle | Project Mgr |
| R9 | Low user adoption | M | H | Co-design with estimators, champions, shadow pilot, visible time savings | Project Mgr |
| R10 | AI processing cost exceeds budget | M | M | Per-bid cost tracking, deterministic first, caching, model tiering | Tech Lead |
| R11 | ERP integration is complex | M | M | Start with file-based import; move to API later | Tech Lead |
| R12 | Standards licences restrict ingestion | M | M | Confirm licence terms; fall back to clause references entered by the knowledge owner | Design Mgr |
| R13 | Vendor or model lock-in, or a provider outage or change in price or terms | M | M | Multi-provider AI gateway with configurable routing and fallbacks (§12.2); golden-set model comparison before any switch; an approved fallback provider for each data class where ADR-004 allows | Tech Lead |

L = likelihood, I = impact (H / M / L).

## 17. Open Questions and Decisions

| # | Question / decision | Owner | Needed by |
|---|---|---|---|
| D1 | Confirm the MVP scope (§13.1): wet-pipe sprinkler QTO and BOQ; rate-library pricing as a Should; labour in P2. | Sponsor | P0 start |
| D2 | Approve Singapore-region cloud hosting, and each LLM provider's data terms and the data classes it may receive (§12.2). | Sponsor / IT | P0 start |
| D3 | Provide the golden dataset and nominate a data owner. | Estimating Mgr | P0 week 2 |
| D4 | Identify the ERP/accounting system and whether historical cost and labour-hour data exists. | Finance / Ops | P0 |
| D5 | Name the approvers for gates G0–G4. | Sponsor | P0 |
| Q6 | What share of tenders are design and build rather than build-only? This sets the priority of design-risk features. | Bid Manager | P0 |
| Q7 | Annual tender volume, typical sheet counts and turnaround times, for sizing and ROI. | Bid Manager | P0 |
| Q8 | Do typical tender confidentiality terms allow cross-bid learning from derived data? | Legal | P0 |
| Q9 | What are the company's standard BOQ structure and measurement conventions? | Estimating Mgr | P0 |
| Q10 | Which client BOQ formats and QS firms are most common, to prioritise templates? | Estimating Mgr | P1 |
| Q11 | Are NFPA/FM-based projects (e.g. data centres, industrial) a target segment? This affects system coverage and the corpus. | Sponsor | P1 |
| Q12 | Build, buy or partner for the takeoff engine? Compare commercial takeoff tools against fire-protection-specific needs. | Project Mgr / Tech Lead | P0 |

## Appendix A. Glossary

| Term | Meaning |
|---|---|
| AVL | Approved vendor / makes list in the project specification |
| BOQ | Bill of quantities |
| Breeching inlet | Inlet through which SCDF appliances pump water into rising mains or the sprinkler system (US: FDC) |
| CORENET X | Singapore's integrated regulatory submission platform for building plans |
| D&B | Design and build, where the contractor carries design responsibility |
| FSC | Fire Safety Certificate issued by SCDF |
| FSE | Fire Safety Engineer |
| G0–G4 | Human approval gates defined in §5 |
| Golden dataset | Historical tenders with verified QTO and BOQ, used to evaluate accuracy |
| IFC-SG | Singapore's IFC-based openBIM data standard for regulatory submissions |
| Landing valve | Outlet valve on a rising main at each floor |
| LD | Liquidated damages |
| MC | Main contractor |
| QP | Qualified Person: registered architect or professional engineer submitting plans |
| QTO | Quantity takeoff |
| RI | Registered Inspector |
| Rising main | Vertical fire-fighting water main, dry or wet (US: standpipe) |
| SCDF | Singapore Civil Defence Force |
| Tender clarification | Pre-award query to the client. Distinct from a construction RFI (post-award). |
| Vector PDF | PDF containing drawing geometry as vector paths rather than a scanned image |

## Appendix B. Example QTO Evidence Record

This expands the v1 example (v1 §8) to the full set of mandatory fields (FR-QTO-09) and shows client BOQ reconciliation.

| Field | Example value |
|---|---|
| QTO ID | QTO-000347 |
| Bid / project | BID-2026-014 / Example Commercial Tower |
| Item and classification | Fire main, 150 mm: main, wet sprinkler system |
| Material / schedule / joining | Galvanised steel, Sch 40, grooved (per Spec cl. 4.2.1, Rev B) |
| Quantity / unit | 128.4 m net measured. Wastage (5%) applied separately at BOQ. |
| Source | FP-L05-201 Rev R04 (Current), page 1 |
| Location | Level 05, Zone B, Grid B5–G5 |
| Geometry reference | DWG polyline handles and sheet bounding box coordinates |
| Detection method | DWG layer polylines, with size annotation confirmed by OCR (confidence 0.99) |
| Calculation method | Sum of centreline lengths at verified scale 1:100. Excludes riser (see QTO-000351). |
| Evidence | Overlay snapshot link; annotation "150Ø" |
| Confidence | 0.97 (calibrated probability that item and quantity are within tolerance) |
| Verification status | Pending estimator verification → Verified by [user] at [timestamp] |
| Linked BOQ line | Client BOQ item 3.2.4, quantity 120 m: variance +7.0% flagged for clarification |
| Run metadata | QTO Agent run ID, model version, rule-set version |

## Appendix C. v1 to v2 Traceability

| v1 section | Disposition | v2 location |
|---|---|---|
| 1 Product Vision | Kept; positioning sharpened | §1.3 |
| 2 Primary Business Scope | Kept; scope boundaries added | §1.4 |
| 3 Target FP Systems | Refined to Singapore terminology; phased | §4 |
| 4 Regulatory and Standards | Kept; labelling table, conflict rule and corpus governance added | §2, §6.8 |
| 5 Tender Document Intelligence | Expanded: quality gate, lineage, revision comparison | §6.2 |
| 6 Fire Drawing Vision | Expanded: legend mapping, scale calibration, overlap awareness | §6.3 |
| 7 Automated QTO | Expanded: rule-derived vertical quantities, fittings, de-duplication, delta QTO | §6.4 |
| 8 Example QTO Record | Expanded with full fields and BOQ reconciliation | Appendix B |
| 9 Specification Agent | Expanded: scope and interface matrix | §6.7 |
| 10 BIM / Digital Coordination | Merged into coordination; Phase 3 | §6.9 |
| 11 Multi-Discipline Coordination | Kept; geometry-evidence rule added | §6.9 |
| 12 RFI and Clarification | Split into tender clarification vs construction RFI; register and lifecycle added | §6.10, §7 |
| 13 Approval State Machine | Replaced by five state models; v1 states kept as the external tracking model | §7 |
| 14 Singapore Costing | Expanded: FX, GST, validity, provenance | §6.11 |
| 15 Labour Estimation | Expanded: Singapore labour rate build-up | §6.12 |
| 16 Bid Risk | Expanded: contract terms, risk register | §6.13 |
| 17 Agent Swarm | Replaced by an orchestrated catalogue with autonomy levels | §8 |
| 18 End-to-End Workflow | Replaced by a gated process with bid/no-bid, addenda loop and post-tender stage | §5 |
| 19 Human-in-the-Loop | Made actionable through RACI, gates and autonomy levels | §3, §5, §8 |
| 20 Core Guardrails | Turned into a control matrix with tests | §9 |
| 21 Data Architecture | Kept; entities, integrations and data policy added | §11 |
| 22 Technology Pattern | Recast as non-binding recommendations (§12.1); multi-provider LLM decision added in v2.1 (§12.2) | §12.1, §12.2 |
| 23 MVP Scope | Revised to match Phase 1 | §13.1 |
| 24 Phased Roadmap | Phase 0 and exit criteria added | §13.2 |
| 25 Key KPIs | Definitions and proposed targets added | §14 |
| 26 Final Business Requirement | Kept as the product requirement statement | §1.1 |
| 27 Operating Principle | Kept | §1.3 |
| New in v2 | Stakeholders and RACI; verification workbench; BOQ reconciliation; NFRs; assumptions; risk register; decisions | §3, §6.5, §6.6, §10, §15–17 |
