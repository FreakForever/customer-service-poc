"""PII masking for logs and traces (LLD-6, FR-RT-06). Raw text stays only in the conversation store."""

import re
from typing import Any

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_GSTIN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")
# Indian mobile/landline shapes: +91 98765 43210, 98765-43210, 9876543210, 022-2345-6789, demo "+91-90000-000010".
# Never inside ids like SR-2026-00105 or MT-DHD150-23-00417 (guarded by the look-arounds).
_PHONE = re.compile(r"(?<![\w-])(?:\+91[-\s]?|0)?(?:\d{5}[-\s]?\d{5,6}|\d{3,4}[-\s]\d{3,4}[-\s]\d{4}|\d{10})(?![\w-])")
_PARTIAL_PHONE = re.compile(r"(?<![\w-])\d{2}x{4,8}\d{2}(?![\w-])", re.IGNORECASE)   # "98xxxxxx12"


def mask_pii(value: str) -> str:
    value = _EMAIL.sub("[email]", value)
    value = _GSTIN.sub("[gstin]", value)
    value = _PHONE.sub("[phone]", value)
    return _PARTIAL_PHONE.sub("[phone]", value)


def mask_deep(obj: Any) -> Any:
    """Mask every string inside dicts/lists (trace detail and span attrs)."""
    if isinstance(obj, str):
        return mask_pii(obj)
    if isinstance(obj, dict):
        return {k: mask_deep(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [mask_deep(v) for v in obj]
    return obj
