# ADR: Google Cloud SQL PostgreSQL trial for the demonstration

**Current implementation (2026-10-25):** The user has since provisioned the instance and enabled pgvector. Read-only inspection through the existing Auth Proxy verified PostgreSQL 18.6, pgvector 0.8.5 in public, and no application tables or Alembic revision table. Central infrastructure settings now support this local proxy connection. The original selection/provisioning assumptions below are historical; actual trial eligibility, resources, expiry, billing coverage and end-to-end zero cost remain unverified. This factual update does not change the architectural status.

**Date:** 2026-10-25  
**Status:** CONFIRMED intended database host, conditional on eligibility, extension availability, and verified zero-cost feasibility; not provisioned.  
**Decision owner:** [U18](register.md#u18), approval [E13](register.md#e13).  
**Related:** [PostgreSQL authority](postgresql-vs-mysql.md), [pgvector selection](pgvector-selection.md), [Python persistence stack](python-persistence-stack.md).

## Context and requirements

The assignment requires a running public application, free hosting or a free tier, persistence across application restarts, secrets protection, and documented limitations. Purchasing infrastructure must not be necessary. Sleep/request limitations are acceptable only if core functionality can be demonstrated. See [assignment page 6](C:/Users/merli/Downloads/Applied_AI_Investigation_Engine_Task_English.pdf) and the [requirements summary](register.md#assignment-requirements).

The current application has extraction and sequential in-memory registration, not persistent storage or search. The planned database workload combines authoritative documents/versions, exact Content reuse, provenance, transactions, chunks, embeddings, vector similarity, and full-text retrieval. pgvector makes CPU, working memory, storage I/O, and index construction capacity important alongside ordinary relational storage.

The user expects the internship to start less than one month from this decision and intends submission, review, and demonstration before trial expiry. These are user-supplied planning assumptions. The interview date is unconfirmed; no reviewer commitment or hosting extension has been established.

## Decision

Use the **Google Cloud SQL for PostgreSQL free trial instance** as the intended managed database platform for the assignment demonstration, subject to validation below. Activate it near deployment, after local design and ingestion preparation are ready, so preparation does not consume the serving window unnecessarily.

This selects database hosting only. It does not select backend/frontend hosting or authorize cloud provisioning, network configuration, paid services, or deployment automation. This is a time-bounded demonstration choice, not an indefinite free production-hosting claim.

## Alternatives considered

| Option | Relevant advantages | Trade-offs for this project |
| --- | --- | --- |
| Cloud SQL PostgreSQL trial | Advertised dedicated trial capacity for testing combined relational/vector workloads; managed PostgreSQL. | Short serving period, billing eligibility, network costs, and trial backup limitations. Selected conditionally for the intended demonstration window. |
| Azure Database for PostgreSQL | Managed PostgreSQL and pgvector support; eligible free accounts have a longer limited-resource offer. | Offer-specific Burstable capacity, quotas, eligibility, and ancillary charges must be checked. No performance comparison was run. |
| Neon PostgreSQL | An ongoing Free plan can avoid a single short trial deadline. | Compute/storage/transfer quotas and scale-to-zero behavior require capacity/latency validation for embeddings and indexes. |
| Other free managed PostgreSQL offerings | May provide a viable extension-compatible fallback. | No additional provider selected; offers must be evaluated against the same capacity, cost, expiry, and connectivity constraints. |

Microsoft advertises up to 750 Burstable compute hours and 32 GB storage monthly for 12 months for eligible Azure free accounts, and pgvector support. Neon currently documents 1 GB storage per Free project, 100 CU-hours per project, and scale-to-zero after inactivity. These are dated comparison facts, not promises of this user's eligibility or sufficient vector capacity. [Azure PostgreSQL](https://azure.microsoft.com/en-us/products/postgresql/), [Neon plans](https://neon.com/docs/introduction/plans).

## Verified trial specifications and extension capability

Google's official material confirms the specific preset PostgreSQL trial offer:

| Item | Published offer |
| --- | --- |
| Compute | 8 vCPUs |
| Memory | 64 GB |
| Durable storage | 100 GB |
| Serving trial | Up to 30 days |

[Google Cloud free offers](https://cloud.google.com/free). These figures do not describe every Cloud SQL instance. The detailed trial configuration is Enterprise Plus/N2 with a separate 375 GB data cache; preset resources cannot be freely resized during the trial. Do not count cache as extra durable storage. [Trial configuration](https://docs.cloud.google.com/sql/docs/postgres/free-trial-instance).

Cloud SQL lists pgvector 0.8.5 for PostgreSQL 13 and later, with different versions for older PostgreSQL releases. Google also documents pgvector HNSW and IVFFlat indexing. This establishes product-level availability, not a completed test on an actual trial instance. [Supported extensions](https://docs.cloud.google.com/sql/docs/postgres/extensions), [vector indexing](https://docs.cloud.google.com/sql/docs/postgres/generate-manage-vector-embeddings).

Before provisioning/ingestion, verify the offered PostgreSQL version, region, extension version, extension-creation permissions, and required index/operator capabilities on the actual trial configuration. No HNSW/IVFFlat configuration has been selected. Cloud-specific embedding-generation or vector-assist services are not needed by this decision and are not selected.

## Free-cost feasibility and provisioning blockers

The documented trial requires a new project with billing enabled; a payment method may be required. It is separate from the general new-customer credit offer. Serving stops after 30 days; the subsequent 90-day grace period retains data but does not serve application requests. Normal backup/restore is unavailable; an optional final backup on deletion is chargeable. No trial SLA applies. [Trial conditions](https://docs.cloud.google.com/sql/docs/postgres/free-trial-instance).

**The database instance offer does not establish a zero-cost deployment.** Google explicitly lists chargeable transfer for public-internet and cross-region trial connections. General Cloud SQL pricing also identifies network egress and idle IPv4 charges. A free app hosted elsewhere must not be assumed to connect for free. Nor does a proxy or language connector by itself establish a free network path. [Trial charges](https://docs.cloud.google.com/sql/docs/postgres/free-trial-instance), [Cloud SQL network/IP pricing](https://cloud.google.com/sql/pricing).

The following are unresolved deployment blockers, not provisioning instructions:

- Establish the user's trial eligibility and offered resources in the intended region.
- Choose and price the complete connection path, including database traffic, public IP treatment, client/source egress, any private-network resources, and authentication/TLS requirements. Account-specific exemptions or credit coverage have not been verified.
- Demonstrate that necessary application/database connectivity and data migration can operate without requiring paid infrastructure. If a necessary path entails unavoidable out-of-pocket charges, the assignment condition is not met and the hosting/network plan must be revisited before provisioning.
- Confirm an achievable demonstration window and a non-paid contingency if review occurs after expiry. A paid upgrade is not authorized by this ADR.

Database sessions have now been connected read-only through the user-authorized proxy. No billing account, credit allowance, project quota, region availability, public IP charge exemption, or network-cost estimate has been inspected for this user. Costs remain **unresolved**, not zero. A pricing calculator or budget notification alone would not establish that billed usage cannot occur.

## Resource justification and evaluation

The advertised resources provide headroom worth evaluating for bulk ingestion, possible HNSW construction, semantic/hybrid queries, and overlapping investigations. Memory affects working sets and builds; CPU handles search/ranking and relational work; I/O supports ingestion and indexes. This is a workload-based rationale, not a throughput or latency guarantee.

The corpus has 56,680 distinct texts and 125,760 occurrences, but embedding count, dimensions, chunk overlap, index overhead, and sharing rules are still undecided. Capacity must include relational rows, vectors, full-text data, indexes, temporary build space, and operational overhead. No benchmark proves the trial resources sufficient, and no need for paid upgrades is assumed.

Evaluation must record the actual database/version/resources and warm/cold state so that results are attributable to the demonstration environment. Targets, concurrency levels, query configurations, and evaluation design remain U17; backend hosting remains unresolved.

## Portability and migration consequences

Prefer ordinary PostgreSQL connections, SQL, JSONB, and pgvector through the approved SQLAlchemy/psycopg stack. Keep Cloud SQL-specific identity/network setup at infrastructure boundaries. Alembic can reproduce approved schema evolution; pinned/reproducible ingestion can rebuild source and derived data. This reduces provider coupling without making migration automatic.

Migration still needs compatible PostgreSQL and extension versions, available vector operators/types, sufficient destination capacity, transferable data, credentials/network access, and a verified export/import or rebuild strategy. Alembic migrates schema history, not the corpus or vectors. Extension privileges, provider settings, and downtime differ. No export, backup, migration tool, or alternative host is implemented or selected.

## Risks and limitations

- **Expiry/evaluation delay:** Intended timing may slip. A stopped grace-period database cannot power the public demo.
- **Billing/networking:** A selected backend location may create chargeable traffic or supporting resources; feasibility must be established before activation.
- **Data protection:** Do not assume standard managed-service backups apply to this trial. Reproducible ingestion helps rebuild data but does not recover unsaved uploads or investigation history.
- **Connectivity/latency:** Cross-provider routing, access controls, TLS, pool limits, and region distance can constrain the app independently of database compute.
- **Resource/policy limits:** Preset capacity and published offers can change. Unsupported extensions or unavailable trial resources require reconsideration rather than an unauthorized paid substitute.
- **Migration:** Data transfer and rebuild duration/cost can affect the demonstration deadline; plan before expiry. No backup or monitoring implementation is approved here.

## Scope, remaining decisions, and verification

U18 is CONFIRMED only for this conditional intended database-hosting choice. Backend/frontend hosting, networking, provisioning, deployment automation, backups, monitoring, paid services, and concrete pooling configuration remain unresolved. U07/U14 still govern schema/transactions/recovery; U11/U12/U13/U17 govern retrieval configuration and evaluation.

Official sources were checked during the earlier 2026-10-25 investigation, not refreshed during repository cleanup. The user subsequently provisioned the database. Read-only extension/connectivity checks succeeded, but actual trial eligibility, end-to-end zero cost and demonstration timing remain unverified. The decision remains reviewable if a documented blocker cannot be resolved within the assignment's free-hosting requirement.
