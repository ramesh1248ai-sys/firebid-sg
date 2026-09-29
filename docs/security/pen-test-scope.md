# Penetration test scope (NFR-06: annual; first before production)

## Purpose

An independent test of FireBid SG before it holds live tender data in production, and yearly after that. The test checks the controls in [`docs/security-baseline.md`](../security-baseline.md) and the ASVS Level 2 checklist ([`asvs-l2-checklist.md`](asvs-l2-checklist.md)) from an attacker's side.

## Target

- **Environment:** staging on Google Cloud, `asia-southeast1` (ADR-008), with production-equivalent configuration and synthetic data only. No live tender documents in scope.
- **In scope:**
  - the web application (React);
  - the API (`/api/*`);
  - identity through the organisation's IdP (Entra ID) as configured for FireBid;
  - file upload and the parser sandbox;
  - object storage access paths (presigned URLs);
  - the load balancer and Cloud Armor configuration.
- **Out of scope:**
  - the LLM providers' own services;
  - Google Cloud's infrastructure;
  - the IdP tenant beyond FireBid's app registration;
  - denial-of-service volume testing (only rate-limit behaviour is in scope);
  - social engineering.

## What the testers are asked to try

1. **Bid isolation:** reach another bid's documents, quantities, prices or audit history as a member of a different bid, through the API, presigned URLs, or by guessing IDs. Both the application checks and row-level security should stop it.
2. **Role escalation:** perform a gate approval, a rate-library import or a template change without the role (`auth/permissions.py`).
3. **Hostile files:** crafted PDF, DXF, XLSX, DOCX and archive uploads (zip bombs, XXE, traversal names, pixel bombs, polyglots). The expectation is that nothing escapes the sandbox pool, which has no network route and no credentials.
4. **Token handling:** replayed, expired, `alg: none`, wrong-audience and wrong-issuer tokens; session behaviour in shared browsers.
5. **Injection:** SQL, formula injection in exported workbooks, and stored XSS through tender text shown in the workbench.
6. **Price integrity:** set or alter a BOQ price without a rate-library source, which should be refused by the domain and the database.
7. **Audit tamper evidence:** alter history undetected. The nightly chain verification should report it.
8. **LLM data class:** make the platform send confidential or commercial data to a provider not approved for it. The gateway refuses it on every attempt, fallbacks included.

## Rules of engagement

- A named FireBid contact and the IT/security owner are reachable throughout. Testing stops at once if production is affected.
- Test accounts, one per role, are provisioned in the IdP for the test window and removed after.
- Findings are reported with severity (CVSS 3.1 or 4.0), reproduction steps and a suggested fix. High and critical findings block production until fixed and retested.

## Deliverables

A report, a retest of every high and critical finding, and a letter of attestation for the Phase 1 gate file.
