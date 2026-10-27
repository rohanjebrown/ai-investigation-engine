# AI Usage

I used ChatGPT and Codex as development tools throughout this project, primarily for technical research, discussing architectural trade-offs, implementation assistance, and verification.

My approach was to make architectural decisions deliberately, using AI to explore alternatives, identify potential problems, and accelerate implementation rather than accepting generated solutions without review.

## Development Approach

I began by comparing potential datasets and selected **2WikiMultihopQA** because its multi-hop questions provide a suitable way to evaluate evidence retrieval across documents. The dataset contains 125,760 passage occurrences representing 56,680 unique exact-content passages.

I designed the ingestion architecture to remain independent of this dataset so that additional formats could be supported without changing the core document model.

Several decisions required particular attention:

* **Document modeling and deduplication:** I chose to separate Documents, DocumentVersions, and immutable Content. Identical extracted text can be reused through SHA-256, with exact-text verification, while independent documents retain their own identities, provenance, and source locations. I deliberately avoided semantic deduplication because similar documents may contain different evidence.

* **Database architecture:** I selected PostgreSQL with pgvector instead of maintaining separate relational and vector databases. I also set up the Google Cloud PostgreSQL instance and enabled pgvector. For persistence, I chose asynchronous SQLAlchemy with explicit transaction ownership and database-enforced uniqueness to handle concurrent registration and idempotent retries.

* **Chunking:** Rather than adopting a conventional 500–1,000-token chunk size, I examined the corpus characteristics. With a median passage length of approximately 72 tokens, whole-passage retrieval was worth retaining as a baseline. I selected fixed-token windows and sentence-aware packing as additional strategies for comparison. The implementation also accounts for identical Content having different sentence boundaries across source versions.

* **Embedding strategy:** I reconsidered the initial recommendation to use BGE-small with local inference, preferring to investigate hosted APIs to reduce deployment complexity. I configured Gemini API access and used preliminary retrieval experiments to compare chunking approaches and title-aware embeddings. The experiments did not demonstrate a consistent advantage for adding titles, so I kept that decision open rather than introducing additional complexity.

* **Controlling complexity:** I repeatedly reviewed proposed abstractions, database relationships, and indexes before approving implementation. For example, the initial embedding-storage design proposed six tables. Further review produced a simpler five-table proposal that derives chunk Content through DocumentVersion and defers indexing lifecycle state until its requirements are established.

## AI Assistance

I used **ChatGPT** to examine requirements, compare technical approaches, work through database and retrieval design questions, and critically review implementation proposals.

I used **Codex** for implementation assistance across the domain, application, and infrastructure layers, including ingestion adapters, PostgreSQL persistence, Alembic migrations, chunking, documentation, and code cleanup.

I worked iteratively, reviewing proposed changes and requesting revisions when a design introduced unnecessary complexity, made unsupported assumptions, or conflicted with earlier decisions.

## Verification

AI-assisted verification included PostgreSQL migration and constraint checks, concurrent registration scenarios, duplicate-content handling, exact source-offset validation, and full-corpus chunking analysis.

I also used a preliminary eight-question retrieval experiment to inform the embedding and chunking design. Its limited scope was considered when interpreting the results rather than treating it as proof of retrieval quality across the complete corpus.

These checks support the current implementation, while the final evaluation and broader automated testing remain separate work.

The project's architectural direction, technology selections, and acceptance of implementation decisions have been my responsibility. AI has primarily helped me investigate alternatives, implement selected approaches more efficiently, and identify problems requiring further attention.
