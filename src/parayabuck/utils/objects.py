from __future__ import annotations

from typing import Any


def field_value(value: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` from a dict or attribute namespace, returning ``default`` when absent.

    Alpaca SDK objects vary between dict-like payloads and attribute models
    across versions and endpoints, so callers need a uniform accessor that
    works for both without branching at every callsite.
    """
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


__all__ = ["field_value"]
