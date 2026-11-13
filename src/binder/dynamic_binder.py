from typing import List, Dict, Any
from src.registry.models import ToolMetadata

class JustInTimeToolBinder:
    """
    Transforms retrieved ToolMetadata records into standard LLM function calling schemas
    just-in-time, ensuring only the top candidate tools are bound to the LLM context.
    """
    @staticmethod
    def to_openai_tool_specs(tools: List[ToolMetadata]) -> List[Dict[str, Any]]:
        """Converts ToolMetadata records into standard OpenAI function calling format."""
        specs = []
        for t in tools:
            specs.append({
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters_schema
                }
            })
        return specs

    @staticmethod
    def to_compact_prompt_summary(tools: List[ToolMetadata]) -> str:
        """
        Builds a compact context-efficient text representation of available candidate tools.
        Consumes minimal tokens (~100 tokens per tool instead of thousands).
        """
        lines = []
        for i, t in enumerate(tools, 1):
            req_params = t.parameters_schema.get("required", [])
            req_str = f" [Required: {', '.join(req_params)}]" if req_params else ""
            lines.append(f"{i}. {t.name} ({t.category}){req_str}: {t.description}")
        return "\n".join(lines)
