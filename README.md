# Scalable Agentic System for Massive API Collections

A laptop-friendly prototype for dynamic API discovery, LLM-assisted planning, and controlled execution across a large API catalogue, built around a simulated PayPal service.

## 1. Problem

Exposing an LLM to a large number of API tools simultaneously can increase context usage, tool-selection errors, latency, and inference costs.

This project addresses the problem through semantic retrieval, bounded tool selection, stateful orchestration, and deterministic execution controls.

## 2. Architecture

- **SQLite Tool Registry:** Stores 51 API tool definitions, including their descriptions, parameters, and metadata.
- **ChromaDB + Sentence Transformers:** Uses `all-MiniLM-L6-v2` embeddings to retrieve relevant API candidates through the `api_tools` collection.
- **LangGraph Orchestrator:** Manages multi-step workflows, retrieves candidates for individual subgoals, and maintains workflow state.
- **LLM Planning:** Selects an action from the retrieved candidates using the configured LLM provider.
- **Execution Engine:** Validates parameters, handles execution, and enforces approval gates for sensitive operations.
- **Knowledge RAG:** Retrieves relevant sections from domain documents through a separate `knowledge_docs` collection.
- **System Search:** Queries registered capabilities and execution audit history from SQLite.

The PayPal service is simulated. The registry contains 51 definitions, but only 17 operations have local mock handlers. Other registered operations are not implemented endpoints.

## 3. Technology Stack

| Technology | Purpose |
|---|---|
| Python | Application runtime |
| LangGraph | Stateful agent orchestration |
| LangChain | LLM integration |
| ChromaDB | Vector storage and retrieval |
| Sentence Transformers | Semantic embeddings |
| SQLite | Tool registry and execution history |
| Pydantic | Parameter validation |
| Streamlit | Interactive interface |

## 4. Project Structure

- `src/` – Registry, retrieval, orchestration, validation, execution, and auxiliary tools.
- `tests/` – Automated tests.
- `data/docs/` – Knowledge-base Markdown documents.
- `data/system.db` – Clean SQLite registry database.
- `data/chroma/` – Prebuilt tool and knowledge vector indexes.
- `main.py` – CLI entry point.
- `streamlit_app.py` – Streamlit interface.
- `ARCHITECTURE.md` – Detailed architecture.
- `DECISIONS.md` – Architectural decisions.

## 5. Installation

Requires Python 3.11 or later.

Create and activate a virtual environment:

```powershell
python -m venv venv
venv\Scripts\activate
```

Install dependencies:

```powershell
pip install -r requirements.txt
```

Configure the environment:

```powershell
Copy-Item .env.example .env
```

The default provider is `mock`, which supports offline deterministic planning without an LLM API key.

The first embedding-model download may require internet access if the model is not already cached.

## 6. Running the Application

### CLI

```powershell
$env:LLM_PROVIDER = "mock"
python .\main.py
```

For detailed per-step traces:

```powershell
python .\main.py --verbose
```

### Streamlit

```powershell
$env:LLM_PROVIDER = "mock"
python -m streamlit run .\streamlit_app.py
```

The interface provides three workspaces:

- Agent Chat
- Knowledge RAG
- System Search

## 7. Rebuilding the Indexes

The repository includes a prebuilt SQLite registry and ChromaDB indexes.

To rebuild the registry:

```powershell
python -m src.registry.init_registry
```

To rebuild the tool index:

```powershell
python -m src.retrieval.index_tools
```

To rebuild the knowledge index:

```powershell
python -m src.retrieval.index_docs
```

Run these commands from the project root with the intended storage paths configured.

## 8. Running Tests

```powershell
python -m pytest -q
```

## 9. Demonstration Scenarios

1. Invoice creation and sending with human approval.
2. Monthly sales-volume queries.
3. Dispute-related enquiries.
4. Knowledge-base questions about fees and policies.
5. System searches for available invoice capabilities and execution status.

## 10. Scope and Limitations

- PayPal operations are simulated through local mock handlers; no live PayPal Sandbox integration is included.
- The 51-entry registry is not equivalent to 51 fully implemented API handlers.
- LLM planning requires the corresponding provider credentials when using a live provider.
- The mock provider supports deterministic offline testing.
- The project is a prototype demonstrating scalable tool discovery and orchestration, not a production payment integration.

## Documentation

- [Architecture](ARCHITECTURE.md)
- [Architectural Decisions](DECISIONS.md)
