# Architectural Decision Records (ADRs)

This document records the key architectural and technical decisions made during the project lifecycle, capturing the problem context, options evaluated, chosen approach, trade-offs, and limitations.

---

## ADR-001: Centralized Dynamic Registry vs. Agent-per-API Swarm
- **Status:** Approved
- **Date:** 2026-11-10

### Context
Managing 50+ to 1,000+ APIs cannot be achieved by creating one AI agent for every API (an anti-pattern causing massive routing overhead, latency, and context bloat) or dumping all OpenAPI specs into one prompt.

### Decision
Store APIs as metadata records in SQLite and vector representations in ChromaDB. Dynamically retrieve and bind only the top 3–5 relevant tools into a single orchestration graph per step.

### Trade-offs & Consequences
- **Pros:** Constant $O(1)$ token cost per turn; zero code changes to add APIs; minimizes LLM attention confusion.
- **Cons:** Requires maintaining a vector index and retrieval pipeline.

---

## ADR-002: Orchestration Framework: LangGraph
- **Status:** Approved
- **Date:** 2026-11-10

### Context
We need a framework capable of cyclic reasoning, multi-step tool execution, explicit state management, dynamic tool binding, and human approval interrupts.

### Decision
LangGraph.

### Trade-offs & Consequences
- **Pros:** Deterministic graph control, explicit state typing (`TypedDict`), native cyclic transitions, and interrupt capabilities for human approval. Avoids opinionated multi-agent swarm frameworks (CrewAI, AutoGen) that encourage agent bloat.
- **Cons:** Requires explicit graph state and node definition.

---

## ADR-003: Vector Database Choice: ChromaDB with Dual Collections
- **Status:** Approved
- **Date:** 2026-11-10

### Context
The system has two distinct retrieval requirements:
1. Dynamic API tool discovery.
2. Knowledge base document retrieval (RAG).
Mixing API contracts with documentation chunks causes semantic pollution (e.g. documentation on dispute rules returning an API tool instead of policy text).

### Decision
Use ChromaDB with two strictly isolated collections:
- `api_tools`: Stores API descriptions, intent trigger phrases, and metadata.
- `knowledge_docs`: Stores chunked documentation, guides, and policies.

### Trade-offs & Consequences
- **Pros:** Embedded, zero-infrastructure setup; clean boundary between functional capabilities and informative text.
- **Cons:** Managing two separate vector indices.

---

## ADR-004: Embedding Model: Sentence Transformers (`all-MiniLM-L6-v2`)
- **Status:** Approved
- **Date:** 2026-11-10

### Context
We need a fast, lightweight embedding model capable of running locally without per-token API costs or external network dependencies.

### Decision
`all-MiniLM-L6-v2` from Sentence Transformers.

### Trade-offs & Consequences
- **Pros:** Compact size (~80MB), fast CPU inference, 384-dimensional dense vectors, zero external API costs.
- **Cons:** Requires `sentence-transformers` library.

---

## ADR-005: Metadata & Audit Store: SQLite
- **Status:** Approved
- **Date:** 2026-11-10

### Context
We require relational storage for API tool contracts (endpoints, methods, schemas) and runtime execution logs.

### Decision
SQLite (`data/system.db`) with two tables: `tools` and `execution_logs`.

### Trade-offs & Consequences
- **Pros:** Zero-configuration, ACID compliant, embedded in Python standard library, fast querying, portable.
- **Cons:** Single-file database not designed for high-concurrency multi-node writes (acceptable for an embedded agent system).

---

## ADR-006: Separation of API Tool Discovery vs. Knowledge RAG
- **Status:** Approved
- **Date:** 2026-11-10

### Context
The assignment explicitly requires a separate RAG Pipeline Tool for querying product documentation and a separate discovery mechanism for executable APIs.

### Decision
Expose `rag_pipeline_tool` as a distinct capability that retrieves from `knowledge_docs` and passes relevant excerpts to the configured LLM for grounded answers. Keep the collection completely separate from the API registry. In explicit mock mode, return a labeled extractive passage instead of claiming LLM generation.

### Trade-offs & Consequences
- **Pros:** Modular separation of concerns; prevents the LLM from attempting to execute documentation chunks as APIs.
- **Cons:** Requires intent routing to decide when to call RAG vs API discovery; generated answers require model configuration.

---

## ADR-007: Decoupled Deterministic Execution Engine with Pydantic
- **Status:** Approved
- **Date:** 2026-11-10

### Context
Financial operations cannot rely on the LLM to execute API calls directly. Hallucinations or malformed parameters can cause critical errors.

### Decision
Strictly decouple the LLM from execution:
- **LLM Responsibility:** Propose intent and candidate arguments.
- **Execution Engine Responsibility:** Independent Python runtime that validates arguments against Pydantic models, checks permissions, enforces sensitivity checks, and executes the API.

---

## ADR-008: Prepare vs. Execute Lifecycle and Human-in-the-Loop Approval
- **Status:** Approved
- **Date:** 2026-11-10

### Context
Operations such as sending an invoice, issuing a refund, or capturing a payment have immediate real-world consequences.

### Decision
Enforce a two-stage lifecycle:
- Safe/Read-only tools (`list_disputes`, `get_transaction_summary`, `create_draft_invoice`): execute automatically.
- Sensitive tools (`send_invoice`, `refund_payment`, `capture_payment`): enter a **PREPARED** state, requiring explicit confirmation before final execution.

---

## ADR-009: Stateful High-Fidelity Mock PayPal Service
- **Status:** Approved
- **Date:** 2026-11-10

### Context
To test 50+ APIs without incurring network failure risks, rate limits, or requiring real merchant credentials during an interview demo, we need an execution sandbox.

### Decision
Maintain a SQLite catalog of 51 PayPal API definitions and a stateful Python mock for representative scenario operations. Other registered tools return `NOT_IMPLEMENTED_IN_MOCK`; this project does not connect to PayPal.

### Trade-offs & Consequences
- **Pros:** Deterministic, self-contained demonstrations without merchant credentials or real financial side effects.
- **Cons:** The mock does not implement all catalog entries and is not interchangeable with the real PayPal API.

---

## ADR-010: Separation of Development Logs vs Runtime Audit Traces
- **Status:** Approved
- **Date:** 2026-11-10

### Context
Mixing engineering project notes with runtime tool execution logs creates cluttered, unreadable files.

### Decision
Maintain two distinct, purpose-built logging systems:
- `PROJECT_LOG.md`: Markdown log recording chronological engineering milestones, architectural decisions, bugs, and interview explanations.
- `data/system.db`: `execution_logs` retains a latest-per-request summary; `execution_events` records individual steps with unique IDs. Runtime audit records include tool, arguments, approval status, response, and latency.

---

## ADR-011: Model-Assisted Planning Over Retrieved Candidates
- **Status:** Implemented for planning; provider-backed calls require user configuration
- **Date:** 2026-11-11

### Context
The original planner selected workflows and extracted arguments with rules and regular expressions. That demonstrated deterministic examples but did not demonstrate the assignment's agent choosing among a large set of APIs.

### Decision
Use the configured LangChain chat model to classify requests and propose one next API action at a time. Give it only the top-K tools retrieved from ChromaDB, with each candidate's schema from SQLite. After each execution result, plan again with the accumulated scratchpad so generated identifiers can be used by dependent steps. Keep `LLM_PROVIDER=mock` as an explicit deterministic offline mode.

### Trade-offs & Consequences
- **Pros:** Model-based tool selection and argument planning, bounded tool context, and replanning with real intermediate results.
- **Cons:** OpenAI/Gemini planning requires configured credentials and incurs model latency/cost. Offline mode remains heuristic and does not provide model reasoning. Provider-backed calls have not yet been smoke-tested against external APIs.

---

## ADR-012: Append-Only Execution Events and Explicit Approval
- **Status:** Implemented
- **Date:** 2026-11-11

### Context
The original `execution_logs` table used the request `trace_id` as its primary key. Multiple operations in one workflow therefore replaced one another. The CLI also defaulted a sensitive-action prompt to `yes`.

### Decision
Add an `execution_events` table keyed by a unique `execution_id`, with a shared `trace_id` grouping workflow steps. Keep the existing `execution_logs` table as a compatibility summary and retain its historical rows. Remove the CLI approval default so a sensitive operation runs only after an explicit `yes` response.

### Trade-offs & Consequences
- **Pros:** Every step and approval state is retained; old rows remain readable; no schema-destructive migration is needed.
- **Cons:** The legacy summary table still stores only the latest event for a request. New detailed history readers must use `execution_events`. Automatic retries remain disabled because the current mock adapter has no transient network failures and several actions are non-idempotent.

---

## ADR-013: Grounded Document Answers with an Explicit Offline Mode
- **Status:** Implemented
- **Date:** 2026-11-11

### Context
Retrieval alone returns useful excerpts and sources but does not complete the assignment's RAG question-answering flow. The project also needs reproducible tests that do not call an external model.

### Decision
Keep `knowledge_docs` as a separate ChromaDB collection. Filter retrieved chunks with a configurable relevance threshold, provide the relevant excerpts to the configured model with an evidence-only answer instruction, and attach source references from retrieved metadata. Abstain when no chunks pass the threshold or the model reports insufficient evidence. In mock mode, return the top retrieved passage as an explicitly labeled extractive result.

### Trade-offs & Consequences
- **Pros:** Provider mode implements retrieval plus generated grounded response and source attribution; tests and demos retain an offline path.
- **Cons:** Offline mode is extractive, not generative. The instruction to use evidence reduces unsupported answers but cannot guarantee perfect grounding for every model response.

---

## ADR-014: Report Measured Retrieval Scope and Limitations
- **Status:** Implemented
- **Date:** 2026-11-11

### Context
Earlier documentation treated candidate count as constant token cost and described historical retrieval timings as evidence of model selection quality. Those measurements do not include provider-backed LLM choices or full endpoint execution.

### Decision
Report current synthetic retrieval measurements with their test command and environment limitations. State that candidate count is bounded by `TOP_K_TOOLS`, while actual prompt-token use depends on schema sizes. Report target discovery among the returned candidates, not as top-rank precision or live LLM selection accuracy.

### Trade-offs & Consequences
- **Pros:** Interview claims are reproducible and match what the benchmark measures.
- **Cons:** The benchmark does not establish production-scale latency or LLM decision accuracy; those remain future evaluation work.

---

## ADR-015: Retrieve API Candidates Per Workflow Subgoal
- **Status:** Implemented; measured retrieval benchmarks remain single-query synthetic evaluations
- **Date:** 2026-11-12

### Context
The graph originally retrieved candidates once from the full user request and reused that set for every subsequent action. A later dependent API can be semantically distant from the original query or absent from the initial Top-K set.

### Decision
Before each API planning turn, use the original request, successful execution history, and prior outputs to identify the next remaining subgoal. Query the existing semantic retriever with that subgoal and pass only its fresh bounded candidates to the action planner. Validate the chosen operation against that exact candidate set. If no candidates are retrieved, stop and ask for clarification. An action awaiting approval continues through the existing direct approval resolver; no subgoal planning or retrieval occurs until that prepared action has been executed.

### Trade-offs & Consequences
- **Pros:** Later steps can retrieve APIs absent from the first candidate set; generated identifiers stay available; the model sees only current-step candidates.
- **Cons:** Provider mode adds a structured subgoal-planning call before each API retrieval. The current synthetic benchmark does not measure live subgoal decomposition or per-step recall. Offline mode uses deterministic query construction and supports only limited decomposition of arbitrary compound requests.

---

## ADR-016: Opt-In, Redacted CLI Workflow Trace
- **Status:** Implemented
- **Date:** 2026-11-12

### Context
Per-step retrieval is part of the graph state but the terminal interface previously showed only the final response and audit-history summary. This made it difficult to demonstrate that each workflow subgoal triggers fresh retrieval and that only current candidates reach the planner.

### Decision
Add `--verbose` to the existing CLI and retain a per-request trace in graph state. Each entry records the subgoal/query, fresh candidate names and relevance scores, planner candidate names, selected API, masked parameters, execution status/HTTP code, prior results, and masked output. Common credential and customer-data patterns are redacted from this display trace. The feature is disabled by default and does not change the existing SQLite audit schema or records.

### Trade-offs & Consequences
- **Pros:** Makes per-step retrieval, candidate handoff, and identifier transfer visible without introducing a UI or changing execution flow.
- **Cons:** Pattern-based redaction is not a complete PII detector. The trace is diagnostic display data, not a durable audit record; the existing audit system remains authoritative for execution events.

---

## ADR-017: Streamlit as a Thin Session-Scoped UI
- **Status:** Implemented
- **Date:** 2026-11-12

### Context
The project had a terminal CLI but no graphical interface. The UI must demonstrate existing API discovery/execution, approval, retrieval trace, Knowledge RAG, and System Search without duplicating the agent.

### Decision
Add one Streamlit entry point that delegates Agent Chat to `ScalableAgentOrchestrator.run()` and presents the existing RAG and System Search tools in separate workspaces. Store one orchestrator instance per browser session in Streamlit session state because pending approval authority is held in the orchestrator instance. Forward Approve/Reject with the exact saved request, returned state, and explicit boolean decision. Treat the chat transcript as presentation only, not planner memory.

### Trade-offs & Consequences
- **Pros:** Small UI layer, keeps LangGraph and mock execution authoritative, and survives normal Streamlit reruns without global approval-state sharing.
- **Cons:** Adds Streamlit as a dependency. Approval cannot survive session loss or server restart, matching the current in-memory approval design. UI conversation history does not provide multi-turn planner memory.
