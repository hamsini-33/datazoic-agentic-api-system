import json
import re
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from src.binder.dynamic_binder import JustInTimeToolBinder
from src.config import settings
from src.registry.models import ToolMetadata


Intent = Literal["RAG_DOCS", "SYSTEM_SEARCH", "API_TOOL", "CLARIFICATION"]

_SUPPORTED_INVOICE_CURRENCIES = {"USD", "EUR", "GBP", "INR"}
_EMAIL_CANDIDATE_RE = re.compile(r"(?<![\w.+-])([\w.+-]+@[\w.-]+)", re.ASCII)
_VALID_EMAIL_RE = re.compile(
    r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}"
)


class IntentDecision(BaseModel):
    intent: Intent
    clarification: Optional[str] = None


class ProposedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    done: bool = Field(description="True only when no further API call is required")
    tool_name: Optional[str] = Field(default=None, description="A tool name from the supplied candidate list")
    arguments: Dict[str, Any] = Field(default_factory=dict)
    clarification: Optional[str] = Field(default=None, description="Question to ask when required information is missing")
    rationale: Optional[str] = None


class ProposedSubgoal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    done: bool = Field(description="True only when no further API action is needed")
    subgoal: Optional[str] = Field(default=None, description="A concise query for retrieving APIs for the next remaining task")
    clarification: Optional[str] = None


class GroundedAnswer(BaseModel):
    answer: str
    evidence_sufficient: bool = Field(description="Whether the supplied document excerpts directly support an answer")


class LLMPlanningClient:
    """Model-backed request planner with an explicit deterministic offline mode."""

    def __init__(self, model: Optional[Any] = None):
        self.provider = settings.LLM_PROVIDER.lower()
        self._model_override = model
        self._structured_cache: Dict[type, Any] = {}

    def _get_structured_model(self, schema: type) -> Any:
        if self._model_override is not None:
            model = self._model_override
        else:
            if self.provider == "mock":
                return None
            if self.provider == "openai":
                if not settings.OPENAI_API_KEY:
                    raise ValueError("LLM_PROVIDER is 'openai' but OPENAI_API_KEY is not configured.")
                from langchain_openai import ChatOpenAI

                model = ChatOpenAI(model=settings.LLM_MODEL, api_key=settings.OPENAI_API_KEY, temperature=0)
            elif self.provider == "gemini":
                if not settings.GOOGLE_API_KEY:
                    raise ValueError("LLM_PROVIDER is 'gemini' but GOOGLE_API_KEY is not configured.")
                from langchain_google_genai import ChatGoogleGenerativeAI

                model = ChatGoogleGenerativeAI(
                    model=settings.LLM_MODEL,
                    google_api_key=settings.GOOGLE_API_KEY,
                    temperature=0,
                )
            else:
                raise ValueError(f"Unsupported LLM_PROVIDER '{self.provider}'. Choose openai, gemini, or mock.")

        if schema not in self._structured_cache:
            self._structured_cache[schema] = model.with_structured_output(schema)
        return self._structured_cache[schema]

    @staticmethod
    def _invoke_structured(model: Any, messages: List[tuple[str, str]], schema: type) -> Any:
        result = model.invoke(messages)
        if isinstance(result, schema):
            return result
        if isinstance(result, dict):
            return schema.model_validate(result)
        raise TypeError(f"Structured model returned {type(result).__name__}, expected {schema.__name__} or dict.")

    def classify_intent(self, user_request: str) -> str:
        """Route the request using the configured model or explicit mock heuristics."""
        model = self._get_structured_model(IntentDecision)
        if model is None:
            return self._classify_intent_offline(user_request)

        decision = self._invoke_structured(model, [
            ("system", "Classify the user request into exactly one intent. RAG_DOCS is for questions answered from product documentation. SYSTEM_SEARCH is for registered capabilities or execution history. API_TOOL is for actions or data retrieved through registered APIs. CLARIFICATION is for unclear or unsupported requests. Do not execute anything."),
            ("human", user_request),
        ], IntentDecision)
        return decision.intent

    def identify_next_subgoal(
        self,
        user_request: str,
        execution_history: Optional[List[Dict[str, Any]]] = None,
        scratchpad: Optional[Dict[str, Any]] = None,
    ) -> ProposedSubgoal:
        """Describe the next remaining task before retrieving its API candidates."""
        history = execution_history or []
        outputs = scratchpad or {}
        model = self._get_structured_model(ProposedSubgoal)
        if model is None:
            return self._identify_next_subgoal_offline(user_request, history, outputs)

        context = {
            "original_user_request": user_request,
            "successful_execution_history": history,
            "previous_results": outputs,
        }
        proposal = self._invoke_structured(model, [
            ("system", "Identify only the next remaining API subgoal from the original request, completed execution history, and previous results. Return a concise natural-language retrieval query. Use IDs from previous results when describing dependent work. Do not select an API or invent arguments here; the orchestrator will retrieve candidate APIs and ask a separate planner to choose. If all requested API work is complete, set done=true. If the next action cannot be determined, return done=true with a concise clarification."),
            ("human", json.dumps(context, ensure_ascii=False, default=str)),
        ], ProposedSubgoal)
        if not proposal.done and not (proposal.subgoal and proposal.subgoal.strip()):
            return ProposedSubgoal(done=True, clarification="I couldn't determine the next API step. Could you clarify what remains to be done?")
        return proposal

    @staticmethod
    def _identify_next_subgoal_offline(
        user_request: str,
        execution_history: List[Dict[str, Any]],
        scratchpad: Dict[str, Any],
    ) -> ProposedSubgoal:
        """Deterministic query construction for offline mode; not LLM reasoning."""
        if not execution_history:
            return ProposedSubgoal(done=False, subgoal=user_request)

        completed = {entry.get("tool_name", entry.get("subgoal")) for entry in execution_history}
        request_lower = user_request.lower()
        if "invoice" in request_lower and any(word in request_lower for word in ("send", "dispatch")):
            if "send_invoice" in completed and not ("accept" in request_lower and "dispute" in request_lower and "accept_dispute_claim" not in completed):
                return ProposedSubgoal(done=True)
            if "create_draft_invoice" in completed and scratchpad.get("invoice_id"):
                return ProposedSubgoal(
                    done=False,
                    subgoal=f"Send invoice {scratchpad['invoice_id']} to {scratchpad.get('recipient_email', '')}".strip(),
                )
        if "accept" in request_lower and "dispute" in request_lower and "accept_dispute_claim" not in completed:
            dispute_id = scratchpad.get("dispute_id", "the open dispute")
            return ProposedSubgoal(done=False, subgoal=f"Accept {dispute_id}")

        # Offline mode cannot reliably decompose arbitrary compound requests.
        # Preserve history/results in the fresh retrieval query and let the
        # deterministic action planner decide whether another supported step remains.
        completed_summary = ", ".join(str(name) for name in completed if name) or "none"
        output_keys = ", ".join(sorted(scratchpad.keys())) or "none"
        return ProposedSubgoal(
            done=False,
            subgoal=(
                f"Remaining task from: {user_request}. Completed API steps: {completed_summary}. "
                f"Prior result fields available: {output_keys}. Identify the next API capability."
            ),
        )

    @staticmethod
    def _classify_intent_offline(user_request: str) -> str:
        q = user_request.lower()
        rag_keywords = ["policy", "guideline", "guide", "rule", "timeframe", "how do i", "what is the process", "fee schedule", "processing fee", "terms", "deadline", "how long"]
        system_keywords = ["what tools", "which tools", "tools are available", "registered operations", "what happened to my", "status of my last", "last request", "execution history", "audit log"]
        if any(word in q for word in rag_keywords) and not any(word in q for word in ["my last", "last request", "send", "create"]):
            return "RAG_DOCS"
        if any(word in q for word in system_keywords):
            return "SYSTEM_SEARCH"
        if len(q.split()) < 2:
            return "CLARIFICATION"
        return "API_TOOL"

    def plan_next_action(
        self,
        user_request: str,
        candidate_tools: List[Dict[str, Any]],
        scratchpad: Optional[Dict[str, Any]] = None,
        execution_history: Optional[List[Dict[str, Any]]] = None,
        current_subgoal: Optional[str] = None,
    ) -> ProposedAction:
        """Choose the next single API action from retrieved candidates.

        Replanning after each result lets later actions use generated IDs without
        asking the model to guess values that do not exist yet.
        """
        tools = [ToolMetadata.model_validate(tool) for tool in candidate_tools]
        if not tools:
            return ProposedAction(done=True, clarification="I couldn't find a relevant registered API. Could you clarify what you want to do?")

        model = self._get_structured_model(ProposedAction)
        if model is None:
            return self._plan_next_action_offline(user_request, tools, scratchpad or {}, execution_history or [])

        # Include schemas and descriptions only for the bounded retrieval result.
        tool_specs = JustInTimeToolBinder.to_openai_tool_specs(tools)
        context = {
            "user_request": user_request,
            "current_subgoal": current_subgoal or user_request,
            "available_tools": tool_specs,
            "intermediate_results": scratchpad or {},
            "completed_steps": execution_history or [],
        }
        proposal = self._invoke_structured(model, [
            ("system", "Plan only one API call for the current_subgoal, using the original user_request for context. Select the single most directly relevant operation from available_tools; only these retrieved candidates may be selected. Do not invent customer searches or additional operations unless they are genuinely required by the requested workflow. Use only information supplied by the user and intermediate_results, and never fabricate missing parameter values. If the correct API is available but required parameters are missing, still select that API and provide only the known arguments; let the existing execution validator identify missing parameters and request clarification. Provide only arguments supported by the selected API's schema. Use intermediate_results for IDs produced by earlier steps. If the request needs multiple calls, return only the current subgoal's call; the orchestrator will identify and retrieve candidates for the next subgoal after execution. Do not claim an action succeeded. If current_subgoal is already satisfied, return done=true."),
            ("human", json.dumps(context, ensure_ascii=False, default=str)),
        ], ProposedAction)

        allowed = {tool.name for tool in tools}
        if not proposal.done and (not proposal.tool_name or proposal.tool_name not in allowed):
            return ProposedAction(
                done=True,
                clarification="The planner could not select an action from the retrieved API candidates. Please clarify your request.",
            )
        return proposal

    def answer_from_context(self, question: str, chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Answer a knowledge question using only retrieved document excerpts."""
        if not chunks:
            return {
                "answer": "I couldn't find relevant documentation to answer that question.",
                "answer_mode": "no_evidence",
                "evidence_sufficient": False,
            }

        model = self._get_structured_model(GroundedAnswer)
        if model is None:
            # Explicit offline mode is extractive: keep the answer tied to a
            # retrieved passage and label it so it is not confused with LLM QA.
            best = chunks[0]
            return {
                "answer": f"{best['text']}",
                "answer_mode": "extractive_offline",
                "evidence_sufficient": True,
            }

        context = [
            {
                "source": chunk.get("source", "unknown"),
                "section": chunk.get("section", ""),
                "relevance": chunk.get("similarity", 0.0),
                "text": chunk.get("text", ""),
            }
            for chunk in chunks
        ]
        response = self._invoke_structured(model, [
            ("system", "Answer the user's question using only the supplied retrieved document excerpts. Do not use outside knowledge or invent policy details. If the excerpts do not directly support an answer, set evidence_sufficient=false and say that the documents do not provide enough information. Do not invent source names; source references are attached by the system."),
            ("human", json.dumps({"question": question, "retrieved_context": context}, ensure_ascii=False, default=str)),
        ], GroundedAnswer)

        answer = response.answer.strip()
        if not response.evidence_sufficient or not answer:
            answer = "The retrieved documentation does not provide enough evidence to answer that question."
        return {
            "answer": answer,
            "answer_mode": "llm_grounded",
            "evidence_sufficient": response.evidence_sufficient,
        }

    def _plan_next_action_offline(
        self,
        user_request: str,
        tools: List[ToolMetadata],
        scratchpad: Dict[str, Any],
        execution_history: List[Dict[str, Any]],
    ) -> ProposedAction:
        """Small deterministic planner for tests and offline demos; not LLM reasoning."""
        names = {tool.name for tool in tools}
        completed = {entry.get("tool_name", entry.get("subgoal")) for entry in execution_history}
        q = user_request.lower()

        if "invoice" in q and ("send" in q or "dispatch" in q):
            if "invoice_id" in scratchpad:
                if "send_invoice" in completed:
                    return ProposedAction(done=True)
                name, args = "send_invoice", {"invoice_id": scratchpad["invoice_id"]}
            else:
                name = "create_draft_invoice"
                args = self._extract_invoice_arguments(user_request, scratchpad)
        elif any(word in q for word in ["sales volume", "total sales", "revenue"]):
            name = "get_transaction_summary"
            args = {"start_date": "2026-10-01", "end_date": "2026-10-31", "currency": "USD"}
        elif "dispute" in q or "claim" in q:
            name = "list_disputes"
            match = re.search(r"user_\w+", user_request, re.IGNORECASE)
            args = {"customer_id": match.group(0).lower(), "dispute_state": "OPEN"} if match else {"dispute_state": "OPEN"}
        elif "balance" in q:
            name, args = "get_account_balances", {}
        elif "refund" in q:
            name = "refund_captured_payment"
            args = {}
        elif "invoice" in q and any(word in q for word in ["create", "draft"]):
            name, args = "create_draft_invoice", self._extract_invoice_arguments(user_request, scratchpad)
        else:
            return ProposedAction(done=True, clarification="Could you clarify which registered PayPal operation you want to perform?")

        if name == "create_draft_invoice" and "currency" not in args:
            return ProposedAction(
                done=True,
                clarification="I couldn't determine a supported invoice currency. Please specify USD, EUR, GBP, or INR.",
            )
        if name not in names:
            return ProposedAction(done=True, clarification=f"The retrieved API candidates do not include `{name}`. Please clarify the request.")
        if name in completed:
            return ProposedAction(done=True)
        return ProposedAction(done=False, tool_name=name, arguments=args, rationale="Deterministic offline planner")

    @staticmethod
    def _extract_invoice_arguments(user_request: str, scratchpad: Dict[str, Any]) -> Dict[str, Any]:
        email_candidates = []
        for match in _EMAIL_CANDIDATE_RE.finditer(user_request):
            candidate = match.group(1)
            if candidate.endswith("."):
                candidate = candidate[:-1]  # sentence punctuation after an address
            email_candidates.append(candidate)
        email = email_candidates[0] if len(email_candidates) == 1 and _VALID_EMAIL_RE.fullmatch(email_candidates[0]) else None
        amount_matches = list(re.finditer(
            r"(?<!\w)(?:(?P<currency_before>(?!for\b)[A-Z]{3})\s+\$?(?P<amount_before>\d+(?:\.\d{1,2})?)"
            r"|\$(?P<amount_symbol>\d+(?:\.\d{1,2})?)"
            r"|(?P<amount_after>\d+(?:\.\d{1,2})?)\s*(?P<currency_after>[A-Z]{3}|dollars)\b)(?![\w.])",
            user_request,
            re.IGNORECASE,
        ))
        amount = amount_matches[0] if amount_matches else None
        explicit_description = re.search(
            r"\bdescription\b\s*[:=]?\s*(['\"])(.+?)\1",
            user_request,
            re.IGNORECASE | re.DOTALL,
        )
        description_text = None
        if explicit_description:
            description_text = explicit_description.group(2).strip(" \t\r\n.,;:")
        else:
            # Retain the legacy "for Web Design services" form while excluding
            # obvious recipient clauses, email addresses, and amount/currency text.
            for legacy_description in re.finditer(
                r"\bfor\s+([A-Za-z][A-Za-z0-9\s-]*?)(?=$|[.,;])",
                user_request,
                re.IGNORECASE,
            ):
                candidate = legacy_description.group(1).strip(" \t\r\n.,;:")
                if not candidate:
                    continue
                if re.match(r"(?:customer|recipient|to)\b", candidate, re.IGNORECASE):
                    continue
                if "@" in candidate or re.search(r"\d|[$€£]", candidate):
                    continue
                description_text = candidate
                break
        args: Dict[str, Any] = {}
        if email:
            args["recipient_email"] = email
        explicit_currencies = []
        unsupported_currency = False
        for amount_match in amount_matches:
            explicit_currency = amount_match.group("currency_before") or amount_match.group("currency_after")
            if amount_match.group("amount_symbol") is not None:
                explicit_currency = "USD"
            if explicit_currency:
                normalized_currency = "USD" if explicit_currency.lower() == "dollars" else explicit_currency.upper()
                if normalized_currency in _SUPPORTED_INVOICE_CURRENCIES:
                    explicit_currencies.append(normalized_currency)
                else:
                    unsupported_currency = True
        labeled_currency = re.search(
            r"\bcurrency\s*(?:is\s+|[:=]\s*)?([A-Z]{3})\b|\bin\s+([A-Z]{3})\b",
            user_request,
            re.IGNORECASE,
        )
        if labeled_currency:
            labeled_code = (labeled_currency.group(1) or labeled_currency.group(2)).upper()
            if labeled_code in _SUPPORTED_INVOICE_CURRENCIES:
                explicit_currencies.append(labeled_code)
            else:
                unsupported_currency = True
        distinct_currencies = set(explicit_currencies)
        if not unsupported_currency and len(distinct_currencies) == 1:
            args["currency"] = distinct_currencies.pop()
        elif not unsupported_currency and not distinct_currencies:
            args["currency"] = "USD"
        else:
            # Leave unsupported or conflicting explicit currency evidence unresolved.
            # The offline planner turns this into clarification before dispatch.
            pass
        if amount:
            amount_value = next(
                amount.group(group)
                for group in ("amount_before", "amount_symbol", "amount_after")
                if amount.group(group) is not None
            )
            args["amount"] = float(amount_value)
        if description_text:
            args["description"] = description_text
        return args
