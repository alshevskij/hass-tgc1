"""Pure helpers for persistent receipt-notification history."""

from __future__ import annotations

from typing import Any


def restore_seen_ids(stored: Any, current_ids: set[str]) -> set[str]:
    """Restore history, or establish a silent baseline on first installation."""
    if (
        isinstance(stored, dict)
        and isinstance(stored.get("ids"), list)
        and (stored.get("initialized") is True or bool(stored["ids"]))
    ):
        return {str(item) for item in stored["ids"]}
    return set(current_ids)


def find_new_documents(
    documents: list[dict[str, Any]], seen_ids: set[str]
) -> list[dict[str, Any]]:
    """Return documents whose IDs have not been processed yet."""
    return [
        document
        for document in documents
        if str(document["id"]) not in seen_ids
    ]


def serialize_seen_ids(seen_ids: set[str]) -> dict[str, Any]:
    """Serialize a bounded history, including an explicitly initialized empty one."""
    return {"initialized": True, "ids": sorted(seen_ids)[-500:]}
