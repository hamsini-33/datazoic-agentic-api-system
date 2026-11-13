"""Privacy-conscious formatting helpers for opt-in CLI workflow traces."""

import re
from typing import Any


_SENSITIVE_KEY = re.compile(
    r"(?:email|customer|payer|buyer|phone|address|first.?name|last.?name|"
    r"card|bank|account|ssn|tax.?id|birth|token|secret|password|credential|api.?key)",
    re.IGNORECASE,
)
_SAFE_IDENTIFIER_KEYS = {
    "invoice_id", "order_id", "capture_id", "authorization_id", "dispute_id",
    "refund_id", "execution_id", "workflow_step_id", "trace_id",
}
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_USER_ID = re.compile(r"\buser_[A-Z0-9_-]+\b", re.IGNORECASE)
_ISO_DATE_OR_TIME = re.compile(
    r"(?<!\w)(?:"
    r"\d{4}-\d{2}-\d{2}(?:[Tt ]\d{2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?(?:[Zz]|[+-]\d{2}:?\d{2})?)?"
    r"|\d{2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?(?:[Zz]|[+-]\d{2}:?\d{2})?"
    r")(?!\w)"
)
_PHONE = re.compile(r"(?<!\w)(?:\+?\d[\d(). -]{7,}\d)(?!\w)")
_SECRET_ASSIGNMENT = re.compile(
    r"\b(api[_ -]?key|access[_ -]?token|refresh[_ -]?token|token|password|secret|credential)"
    r"\s*[:=]?\s*[^\s,;]+",
    re.IGNORECASE,
)
_BEARER_SECRET = re.compile(r"\bBearer\s+[^\s,;]+", re.IGNORECASE)
_CUSTOMER_LABEL_VALUE = re.compile(
    r"\b(customer|recipient|payer|buyer)\s+(?:name\s+)?(?:is\s+)?[A-Z][A-Z'-]*(?:\s+[A-Z][A-Z'-]*)+",
    re.IGNORECASE,
)
_PERSON_NAME = re.compile(r"\b[A-Z][a-z]+(?:[-'][A-Z]?[a-z]+)?\s+[A-Z][a-z]+(?:[-'][A-Z]?[a-z]+)?\b")
_NAMED_PERSON = re.compile(r"\b(customer|recipient|payer|buyer|user|client|merchant|to|from)\s+(?:is\s+)?([A-Z][a-z]+)\b", re.IGNORECASE)


def redact_text(value: str) -> str:
    """Redact common contact, customer-ID, and credential patterns from display text."""
    value = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
    value = _BEARER_SECRET.sub("Bearer [REDACTED]", value)
    value = _EMAIL.sub("[REDACTED_EMAIL]", value)
    value = _USER_ID.sub("[REDACTED_CUSTOMER_ID]", value)
    # Shield ISO dates/times while checking other digit strings for phone numbers.
    # Replace with alphabetic sentinels so the phone regex cannot partially match
    # date/time components, then restore the original timestamp text afterwards.
    protected_times = []

    def protect_time(match):
        sentinel = f"ISOTIMEPROTECTED{len(protected_times)}VALUE"
        protected_times.append((sentinel, match.group(0)))
        return sentinel

    value = _ISO_DATE_OR_TIME.sub(protect_time, value)
    value = _PHONE.sub("[REDACTED_PHONE]", value)
    for sentinel, timestamp in protected_times:
        value = value.replace(sentinel, timestamp)
    value = _CUSTOMER_LABEL_VALUE.sub(lambda match: f"{match.group(1)} [REDACTED]", value)
    value = _PERSON_NAME.sub("[REDACTED_NAME]", value)
    value = _NAMED_PERSON.sub(lambda match: f"{match.group(1)} [REDACTED_NAME]", value)
    return value


def redact_trace_value(value: Any, key: str = "") -> Any:
    """Return a JSON-friendly trace value with sensitive fields redacted."""
    normalized_key = key.lower()
    if normalized_key not in _SAFE_IDENTIFIER_KEYS and _SENSITIVE_KEY.search(normalized_key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(child_key): redact_trace_value(child, str(child_key)) for child_key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_trace_value(child, key) for child in value]
    if isinstance(value, str):
        return redact_text(value)
    return value

