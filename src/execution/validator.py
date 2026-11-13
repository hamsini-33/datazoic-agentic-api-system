import re
from typing import Dict, Any, List, Optional
from pydantic import BaseModel, Field


INVOICE_SUPPORTED_CURRENCIES = frozenset({"USD", "EUR", "GBP", "INR"})
_INVOICE_EMAIL_RE = re.compile(
    r"[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
    r"@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,63}"
)

class ValidationResult(BaseModel):
    is_valid: bool
    validated_arguments: Dict[str, Any] = Field(default_factory=dict)
    errors: List[str] = Field(default_factory=list)

class ParameterValidator:
    @staticmethod
    def validate(
        parameters_schema: Dict[str, Any],
        arguments: Dict[str, Any],
        *,
        tool_name: Optional[str] = None,
        user_request: str = "",
    ) -> ValidationResult:
        """
        Validates proposed arguments against the tool's JSON Schema specification.
        Checks for missing required fields, type correctness, and enum constraints.
        """
        errors: List[str] = []
        validated: Dict[str, Any] = {}

        properties_declared = "properties" in parameters_schema
        properties = parameters_schema.get("properties") or {}
        required = parameters_schema.get("required", [])

        # 1. Check for required parameters
        for req_param in required:
            if req_param not in arguments or arguments[req_param] is None:
                errors.append(f"Missing required parameter: '{req_param}'.")

        # 2. Type validation & coersion where safe
        type_mapping = {
            "string": str,
            "number": (int, float),
            "integer": int,
            "boolean": bool,
            "array": list,
            "object": dict
        }

        for param_name, param_val in arguments.items():
            if param_name not in properties:
                if properties_declared:
                    allowed = ", ".join(sorted(properties)) or "none"
                    errors.append(
                        f"Unsupported argument: '{param_name}'. Allowed arguments: [{allowed}]."
                    )
                    # Keep the supplied value in the rejected result for diagnostics.
                    validated[param_name] = param_val
                    continue

                # Preserve historical behavior for schemas without a properties field.
                validated[param_name] = param_val
                continue

            spec = properties[param_name]
            expected_type_str = spec.get("type")
            expected_type = type_mapping.get(expected_type_str)

            if expected_type and param_val is not None:
                # Allow numeric strings to convert to float/int
                # bool subclasses int in Python, but JSON Schema treats boolean
                # as a distinct type from integer and number.
                if expected_type_str in ["number", "integer"] and isinstance(param_val, bool):
                    errors.append(
                        f"Parameter '{param_name}' must be of type {expected_type_str}, received bool."
                    )
                elif expected_type_str in ["number", "integer"] and isinstance(param_val, str):
                    try:
                        param_val = float(param_val) if expected_type_str == "number" else int(param_val)
                    except ValueError:
                        errors.append(f"Parameter '{param_name}' must be of type {expected_type_str}, received string '{param_val}'.")
                elif not isinstance(param_val, expected_type):
                    errors.append(f"Parameter '{param_name}' must be of type {expected_type_str}, received {type(param_val).__name__}.")

            if tool_name == "create_draft_invoice" and param_name == "recipient_email" and isinstance(param_val, str):
                if not _INVOICE_EMAIL_RE.fullmatch(param_val):
                    errors.append("Parameter 'recipient_email' must be a valid email address.")

            if tool_name == "create_draft_invoice" and param_name == "currency":
                if not isinstance(param_val, str):
                    errors.append("Parameter 'currency' must be one of USD, EUR, GBP, or INR.")
                else:
                    normalized_currency = param_val.upper()
                    if normalized_currency not in INVOICE_SUPPORTED_CURRENCIES:
                        errors.append("Parameter 'currency' must be one of USD, EUR, GBP, or INR.")
                    else:
                        param_val = normalized_currency

            # Enum validation
            if "enum" in spec and param_val not in spec["enum"]:
                valid_options = ", ".join(spec["enum"])
                errors.append(f"Invalid value for '{param_name}': '{param_val}'. Allowed values: [{valid_options}].")

            validated[param_name] = param_val

        if tool_name == "create_draft_invoice":
            explicit_currencies, unsupported_explicit_currency = ParameterValidator._invoice_currency_evidence(user_request)
            supplied_currency = arguments.get("currency")
            if unsupported_explicit_currency:
                errors.append("The request contains an unsupported explicit invoice currency; specify USD, EUR, GBP, or INR.")
            elif len(explicit_currencies) > 1:
                errors.append("The request contains conflicting invoice currencies; specify one currency.")
            elif explicit_currencies and explicit_currencies != {"USD"} and supplied_currency is None:
                errors.append(
                    f"The request specifies {next(iter(explicit_currencies))}, but no currency argument was supplied."
                )
            elif explicit_currencies and isinstance(supplied_currency, str):
                normalized_currency = supplied_currency.upper()
                if normalized_currency in INVOICE_SUPPORTED_CURRENCIES and normalized_currency not in explicit_currencies:
                    errors.append("The proposed invoice currency conflicts with the currency in the request.")

        return ValidationResult(
            is_valid=len(errors) == 0,
            validated_arguments=validated if len(errors) == 0 else arguments,
            errors=errors
        )

    @staticmethod
    def _invoice_currency_evidence(user_request: str) -> tuple[set[str], bool]:
        """Return explicit currency codes and whether an unsupported code was stated."""
        codes: set[str] = set()
        unsupported = False
        contextual_currency = re.compile(
            r"(?<!\w)(?P<before>[A-Z]{3})\s+\$?\d+(?:\.\d{1,2})?(?![\w.])"
            r"|(?<!\w)\$\s*\d+(?:\.\d{1,2})?(?![\w.])"
            r"|(?<!\w)\d+(?:\.\d{1,2})?\s*(?P<after>[A-Z]{3}|dollars)\b(?![\w.])",
            re.IGNORECASE,
        )
        for match in contextual_currency.finditer(user_request):
            code = match.group("before") or match.group("after")
            if code is None or code.lower() == "dollars":
                codes.add("USD")
                continue
            normalized_code = code.upper()
            if normalized_code == "FOR":
                # The common phrase "for $50" is not a currency-code prefix.
                if "$" in match.group(0):
                    codes.add("USD")
                continue
            if normalized_code in INVOICE_SUPPORTED_CURRENCIES:
                codes.add(normalized_code)
            else:
                unsupported = True

        labeled_currency = re.compile(
            r"\bcurrency\s*(?:is\s+|[:=]\s*)?(?P<code>[A-Z]{3})\b|\bin\s+(?P<in_code>[A-Z]{3})\b",
            re.IGNORECASE,
        )
        for match in labeled_currency.finditer(user_request):
            code = (match.group("code") or match.group("in_code")).upper()
            if code in INVOICE_SUPPORTED_CURRENCIES:
                codes.add(code)
            else:
                unsupported = True
        return codes, unsupported
