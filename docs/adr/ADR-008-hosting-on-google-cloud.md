# ADR-008: Hosting on Google Cloud in Singapore

- **Status:** Proposed; the product owner chose Google Cloud (29 Sep 2026). Hosting approval (decision D2) and ADR-004's provider data terms are still open. Nothing is deployed.
- **Date:** 2026-09-29
- **Deciders:** Sponsor (D2), Tech Lead, IT/Security
- **Requirements:** NFR-03 (availability, maintenance window), NFR-04 (RPO 24 h, RTO 8 h, immutable snapshots), NFR-05 (data residency), NFR-06 (security), NFR-02 (scale); guardrails 8 and 9

## Context

Phase 1 needs a production and a staging environment in a Singapore cloud region, with:
- **separately scalable pools** for the API, the job workers and the parser sandbox;
- **the sandbox's isolation enforced by the platform:** no network route beyond what it must reach, and resource limits;
- **managed PostgreSQL 17** with point-in-time recovery, and a connection pooler sized for all pools;
- **an immutable bucket** for submission snapshots;
- **monitoring and alerts:** availability, job-queue depth, audit partition creation, audit chain breaks.

The code expects PostgreSQL, S3-compatible object storage and OIDC. Nothing in it is specific to a cloud.

## Options

1. **Google Cloud, `asia-southeast1` (Singapore): GKE Autopilot, Cloud SQL, Cloud Storage.**
   - Pro: GKE Sandbox (gVisor) gives each parser pod its own user-space kernel, which closes the per-job network namespace gap recorded in the security baseline (open item 6). FQDN network policies (Dataplane V2) can limit sandbox egress to named hosts. Cloud SQL for PostgreSQL 17 has point-in-time recovery and regional high availability. Cloud Storage bucket lock gives immutable snapshots. Vertex AI can serve Gemini, and Claude, in-region, which bears on D2's "processing in Singapore" question.
   - Con: Cloud Storage speaks the S3 API only through its XML interoperability endpoint with HMAC keys: object store calls need a staging check (put, get, head, presigned URLs). The team's Google Cloud experience is to be confirmed.
2. **AWS `ap-southeast-1`: EKS, RDS, S3 with Object Lock.** Native S3, and Bedrock for Claude. The sandbox needs gVisor or Firecracker set up by hand, where GKE Sandbox is managed.
3. **Azure Southeast Asia: AKS, Azure Database for PostgreSQL, Blob with immutability.** Entra ID sits next door. Azure OpenAI is in-region. Storage is not S3-compatible without a gateway.

## Recommendation

**Option 1, Google Cloud in `asia-southeast1`**, as the product owner chose, built by the Terraform in `infra/terraform`:

- **Network:** one VPC; private GKE and Cloud SQL (private IP only); Cloud NAT for the API and workers' outbound calls (LLM providers, the IdP).
- **Compute:** GKE Autopilot with three workloads, each with its own horizontal autoscaling:
  - **API** (`firebid-api`) behind a global HTTPS load balancer with a Google-managed certificate, TLS 1.2 minimum and Cloud Armor (rate limits, OWASP rules).
  - **Workers** (`firebid-worker`: default and system queues).
  - **Sandbox** (`firebid-sandbox`: the parse queue) under GKE Sandbox (`runtimeClassName: gvisor`). Resource limits are set per pod, and a default-deny egress policy allows only PgBouncer and `storage.googleapis.com`.
- **Database:** Cloud SQL for PostgreSQL 17 (pgvector enabled). Regional high availability in production; point-in-time recovery with 7 days of logs; daily backups kept 30 days; CMEK.
- **Connection pooling:** PgBouncer as a small deployment in the cluster (transaction pooling), sized for every pool's connections within Cloud SQL's limit.
- **Object storage:** Cloud Storage buckets for documents (versioned, CMEK) and for submission snapshots (a retention policy under bucket lock: immutable for the retention period, NFR-04). Access is through the S3 XML API with HMAC keys held in Secret Manager.
- **Secrets:** Secret Manager, read through Workload Identity. No keys in images or manifests.
- **Monitoring:**
  - an uptime check on `/health` during Singapore business hours (NFR-03: 99.5%);
  - alert policies on availability, 5xx rate, job-queue depth, audit partition creation failures and audit chain breaks (log-based metrics from the structured logs);
  - Cloud SQL storage and CPU alerts.
- **Environments:** staging and production as separate projects, from one module, with a remote state bucket per environment.
- **Maintenance:** releases go through `firebid-ops deploy-guard`, which refuses a planned maintenance window within 48 hours of any active bid's submission deadline (NFR-03).

## Consequences

- **What must happen before the Terraform is applied:**
  - decision D2 approves hosting;
  - the Google Cloud organisation, billing and two projects exist;
  - the IdP app registration exists.

  The Terraform is checked (`terraform validate`, `tflint`, Trivy) but has never been applied.
- **Restore drill and game day:** both are rehearsed on the local stack now, and must be rerun in staging for the Phase 1 gate evidence.
- **Cloud Storage's S3 interoperability** needs a staging acceptance run of every object store call. If presigned URLs or `put_once` misbehave, the fallback is a native Cloud Storage adapter behind the existing `ObjectStore` protocol.
- **Keycloak** is for development only. Staging and production use Entra ID (ADR-001).
- **Costs:** Autopilot bills per pod resource. The sandbox pool scales to zero when the parse queue is empty.
