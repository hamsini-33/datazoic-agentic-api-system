import sqlite3
import json
import datetime
import uuid
from pathlib import Path
from typing import List, Optional, Dict, Any
from src.registry.models import ToolMetadata, ToolCategory, HTTPMethod

class ToolRegistryDB:
    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def init_db(self):
        """Initializes tables for tool registry and execution history."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS tools (
                    tool_id TEXT PRIMARY KEY,
                    name TEXT UNIQUE NOT NULL,
                    category TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    http_method TEXT NOT NULL,
                    description TEXT NOT NULL,
                    parameters_schema TEXT NOT NULL,
                    is_sensitive INTEGER NOT NULL DEFAULT 0,
                    required_permissions TEXT,
                    trigger_phrases TEXT,
                    created_at TEXT NOT NULL
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tools_category ON tools(category)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_tools_name ON tools(name)")

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS execution_logs (
                    trace_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    user_request TEXT NOT NULL,
                    retrieved_tools TEXT,
                    selected_tool TEXT,
                    arguments TEXT,
                    approval_status TEXT,
                    http_status INTEGER,
                    response TEXT,
                    latency_ms REAL
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_logs_timestamp ON execution_logs(timestamp)")

            # Append-only per-action history. Keep execution_logs as the legacy
            # latest-per-request summary so existing databases and callers remain usable.
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS execution_events (
                    execution_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    user_request TEXT NOT NULL,
                    retrieved_tools TEXT,
                    selected_tool TEXT,
                    arguments TEXT,
                    approval_status TEXT,
                    http_status INTEGER,
                    response TEXT,
                    latency_ms REAL
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_trace ON execution_events(trace_id, timestamp)")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_timestamp ON execution_events(timestamp)")
            conn.commit()

    def register_tool(self, tool: ToolMetadata):
        """Inserts or updates an API tool contract in SQLite."""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO tools (
                    tool_id, name, category, endpoint, http_method,
                    description, parameters_schema, is_sensitive,
                    required_permissions, trigger_phrases, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tool_id) DO UPDATE SET
                    name=excluded.name,
                    category=excluded.category,
                    endpoint=excluded.endpoint,
                    http_method=excluded.http_method,
                    description=excluded.description,
                    parameters_schema=excluded.parameters_schema,
                    is_sensitive=excluded.is_sensitive,
                    required_permissions=excluded.required_permissions,
                    trigger_phrases=excluded.trigger_phrases
            """, (
                tool.tool_id,
                tool.name,
                tool.category.value if isinstance(tool.category, ToolCategory) else tool.category,
                tool.endpoint,
                tool.http_method.value if isinstance(tool.http_method, HTTPMethod) else tool.http_method,
                tool.description,
                json.dumps(tool.parameters_schema),
                1 if tool.is_sensitive else 0,
                json.dumps(tool.required_permissions),
                json.dumps(tool.trigger_phrases),
                tool.created_at or now
            ))
            conn.commit()

    def register_tools_bulk(self, tools: List[ToolMetadata]):
        with self.get_connection() as conn:
            for tool in tools:
                self.register_tool(tool)

    def _row_to_model(self, row: sqlite3.Row) -> ToolMetadata:
        return ToolMetadata(
            tool_id=row["tool_id"],
            name=row["name"],
            category=ToolCategory(row["category"]),
            endpoint=row["endpoint"],
            http_method=HTTPMethod(row["http_method"]),
            description=row["description"],
            parameters_schema=json.loads(row["parameters_schema"]),
            is_sensitive=bool(row["is_sensitive"]),
            required_permissions=json.loads(row["required_permissions"] or "[]"),
            trigger_phrases=json.loads(row["trigger_phrases"] or "[]"),
            created_at=row["created_at"]
        )

    def get_tool(self, tool_id: str) -> Optional[ToolMetadata]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tools WHERE tool_id = ?", (tool_id,))
            row = cursor.fetchone()
            return self._row_to_model(row) if row else None

    def get_tool_by_name(self, name: str) -> Optional[ToolMetadata]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tools WHERE name = ?", (name,))
            row = cursor.fetchone()
            return self._row_to_model(row) if row else None

    def list_tools(self, category: Optional[str] = None) -> List[ToolMetadata]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if category:
                cursor.execute("SELECT * FROM tools WHERE category = ? ORDER BY name ASC", (category,))
            else:
                cursor.execute("SELECT * FROM tools ORDER BY category, name ASC")
            return [self._row_to_model(row) for row in cursor.fetchall()]

    def count_tools(self) -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM tools")
            return cursor.fetchone()[0]

    def search_tools_keyword(self, query: str, limit: int = 5) -> List[ToolMetadata]:
        keywords = [k.strip().lower() for k in query.split() if len(k.strip()) > 2]
        if not keywords:
            return self.list_tools()[:limit]

        with self.get_connection() as conn:
            cursor = conn.cursor()
            conditions = []
            params = []
            for kw in keywords:
                conditions.append("(LOWER(name) LIKE ? OR LOWER(description) LIKE ? OR LOWER(trigger_phrases) LIKE ?)")
                p = f"%{kw}%"
                params.extend([p, p, p])

            sql = f"SELECT * FROM tools WHERE {' OR '.join(conditions)} LIMIT ?"
            params.append(limit)
            cursor.execute(sql, tuple(params))
            return [self._row_to_model(row) for row in cursor.fetchall()]

    def log_execution(
        self,
        trace_id: str,
        user_request: str,
        retrieved_tools: List[str],
        selected_tool: Optional[str],
        arguments: Dict[str, Any],
        approval_status: str,
        http_status: int,
        response: Dict[str, Any],
        latency_ms: float,
        execution_id: Optional[str] = None,
    ) -> str:
        """Append an immutable execution event and update the legacy summary row."""
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        event_id = execution_id or f"exec_{uuid.uuid4().hex}"
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO execution_events (
                    execution_id, trace_id, timestamp, user_request, retrieved_tools,
                    selected_tool, arguments, approval_status, http_status, response, latency_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                event_id, trace_id, now, user_request, json.dumps(retrieved_tools),
                selected_tool, json.dumps(arguments), approval_status, http_status,
                json.dumps(response), latency_ms
            ))
            cursor.execute("""
                INSERT OR REPLACE INTO execution_logs (
                    trace_id, timestamp, user_request, retrieved_tools,
                    selected_tool, arguments, approval_status, http_status,
                    response, latency_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trace_id,
                now,
                user_request,
                json.dumps(retrieved_tools),
                selected_tool,
                json.dumps(arguments),
                approval_status,
                http_status,
                json.dumps(response),
                latency_ms
            ))
            conn.commit()
        return event_id

    def get_recent_logs(self, limit: int = 10) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM execution_events ORDER BY timestamp DESC, rowid DESC LIMIT ?", (limit,))
            events = [dict(row) for row in cursor.fetchall()]

            # Include pre-migration records from the old table where no new event
            # exists for that trace. This keeps existing audit history searchable.
            if len(events) < limit:
                event_traces = {row["trace_id"] for row in events}
                cursor.execute("SELECT * FROM execution_logs ORDER BY timestamp DESC LIMIT ?", (limit,))
                for old in cursor.fetchall():
                    row = dict(old)
                    if row["trace_id"] not in event_traces:
                        row["execution_id"] = None
                        events.append(row)
                        event_traces.add(row["trace_id"])
                        if len(events) >= limit:
                            break
            return events[:limit]

    def get_last_execution(self) -> Optional[Dict[str, Any]]:
        logs = self.get_recent_logs(limit=1)
        return logs[0] if logs else None
