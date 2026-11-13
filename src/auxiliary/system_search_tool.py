from typing import Dict, Any, Optional, List
from src.registry.db import ToolRegistryDB

class SystemSearchTool:
    """
    Dedicated tool for introspecting the system itself:
    1. Capabilities Search: What tools/APIs are available for managing invoices, payments, etc.?
    2. Execution History Search: What was the status of my last request? What happened to request X?
    """
    def __init__(self, db: ToolRegistryDB):
        self.db = db

    def run(self, query: str, search_type: str = "auto") -> Dict[str, Any]:
        q_lower = query.lower()

        # 1. Determine Search Type
        if search_type == "auto":
            history_triggers = ["last", "status", "history", "recent", "execution", "happened", "trace", "log", "audit"]
            if any(trig in q_lower for trig in history_triggers):
                search_type = "history"
            else:
                search_type = "capabilities"

        # 2. History & Audit Search Mode
        if search_type == "history":
            logs = self.db.get_recent_logs(limit=5)
            if not logs:
                return {
                    "search_type": "history",
                    "query": query,
                    "found": False,
                    "message": "No matching execution records were found.",
                    "latest_execution": None,
                    "recent_history": [],
                    "logs": [],
                }

            formatted_logs = []
            for log in logs:
                formatted_logs.append({
                    "execution_id": log.get("execution_id"),
                    "trace_id": log["trace_id"],
                    "timestamp": log["timestamp"],
                    "user_request": log["user_request"],
                    "selected_tool": log["selected_tool"],
                    "approval_status": log["approval_status"],
                    "http_status": log["http_status"],
                    "latency_ms": log["latency_ms"],
                    "response": log["response"]
                })

            return {
                "search_type": "history",
                "query": query,
                "found": True,
                "latest_execution": formatted_logs[0] if formatted_logs else None,
                "recent_history": formatted_logs,
                "message": f"Found {len(formatted_logs)} recent execution traces in the audit store."
            }

        # 3. Capabilities & Registered Tools Search Mode
        category = None
        for cat in ["invoicing", "payments", "disputes", "reporting"]:
            if cat in q_lower or cat[:-3] in q_lower: # matches invoice, dispute, etc.
                category = cat
                break

        if category:
            tools = self.db.list_tools(category=category)
        else:
            tools = self.db.search_tools_keyword(query, limit=8)

        formatted_tools = []
        for t in tools:
            formatted_tools.append({
                "name": t.name,
                "category": t.category,
                "endpoint": f"{t.http_method} {t.endpoint}",
                "description": t.description,
                "is_sensitive": t.is_sensitive
            })

        return {
            "search_type": "capabilities",
            "query": query,
            "category_filtered": category,
            "total_matching_tools": len(formatted_tools),
            "tools": formatted_tools,
            "message": f"Discovered {len(formatted_tools)} registered API operations matching your inquiry."
        }
