from enum import Enum
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field, ConfigDict

class ToolCategory(str, Enum):
    INVOICING = "invoicing"
    PAYMENTS = "payments"
    DISPUTES = "disputes"
    REPORTING = "reporting"
    IDENTITY = "identity"

class HTTPMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"
    PATCH = "PATCH"

class ToolMetadata(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    tool_id: str = Field(..., description="Unique machine-readable tool identifier (e.g., 'inv_create_draft')")
    name: str = Field(..., description="Function/API name used in tool calls (e.g., 'create_draft_invoice')")
    category: ToolCategory = Field(..., description="Business domain category")
    endpoint: str = Field(..., description="REST API endpoint route (e.g., '/v2/invoicing/invoices')")
    http_method: HTTPMethod = Field(default=HTTPMethod.POST, description="HTTP method")
    description: str = Field(..., description="Comprehensive explanation of what the tool does and when to call it")
    parameters_schema: Dict[str, Any] = Field(default_factory=dict, description="Standard JSON Schema describing input parameters")
    is_sensitive: bool = Field(default=False, description="Whether this tool mutates financial state or sends external communications")
    required_permissions: List[str] = Field(default_factory=list, description="OAuth scopes / permissions required")
    trigger_phrases: List[str] = Field(default_factory=list, description="Sample user query expressions matching this tool")
    created_at: Optional[str] = None
