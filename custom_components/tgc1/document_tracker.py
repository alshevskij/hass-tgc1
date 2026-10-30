"""Helpers for persistent receipt-notification history."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

_LOGGER = logging.getLogger(__name__)


def _bounded_seen_ids(seen_ids: set[str]) -> set[str]:
    """Keep the 500 highest numeric document IDs."""
    return set(sorted(seen_ids, key=int)[-500:])


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
    return {"initialized": True, "ids": sorted(_bounded_seen_ids(seen_ids), key=int)}


async def async_load_seen_ids(store: Any, current_ids: set[str]) -> set[str]:
    """Load history; fall back to a quiet baseline if storage is unavailable."""
    try:
        stored = await store.async_load()
    except Exception:
        _LOGGER.exception(
            "Не удалось прочитать историю квитанций ТГК-1; "
            "текущие документы будут приняты за исходную историю"
        )
        return set(current_ids)
    return restore_seen_ids(stored, current_ids)


async def async_save_seen_ids(store: Any, seen_ids: set[str]) -> bool:
    """Save bounded history without failing the coordinator refresh on storage errors."""
    bounded_ids = _bounded_seen_ids(seen_ids)
    seen_ids.clear()
    seen_ids.update(bounded_ids)
    try:
        await store.async_save(serialize_seen_ids(bounded_ids))
    except Exception:
        _LOGGER.exception(
            "Не удалось сохранить историю квитанций ТГК-1; "
            "уведомления могут повториться после перезапуска"
        )
        return False
    return True


def process_document(
    document_id: Any,
    seen_ids: set[str],
    create_notification: Callable[[], None],
    fire_event: Callable[[], None],
) -> None:
    """Mark a document as seen only after notification and event processing succeed."""
    create_notification()
    fire_event()
    seen_ids.add(str(document_id))
