"""Audit log — append-only event recording.

Every important decision in the system records an `AuditEvent`. Reads are by
type, correlation_id, or time range.

Payloads are JSON-serialized for flexibility. Schema-discipline is the caller's
responsibility — `record_event` validates only that the payload is JSON-encodable.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from snapd_invest.clock import Clock
from snapd_invest.models import AuditEvent, new_id


async def record_event(
    session: AsyncSession,
    clock: Clock,
    *,
    event_type: str,
    payload: dict[str, Any],
    correlation_id: str | None = None,
) -> AuditEvent:
    """Persist an audit event. Returns the persisted entity.

    The session is flushed but not committed — the caller's transaction owns the
    commit decision.
    """
    if not event_type:
        raise ValueError("event_type must be non-empty")

    try:
        payload_json = json.dumps(payload, default=str, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"payload is not JSON-encodable: {exc}") from exc

    event = AuditEvent(
        id=new_id(),
        type=event_type,
        payload=payload_json,
        correlation_id=correlation_id,
        occurred_at=clock.now(),
    )
    session.add(event)
    await session.flush()
    return event


async def list_events(
    session: AsyncSession,
    *,
    event_type: str | None = None,
    correlation_id: str | None = None,
    since: datetime | None = None,
    limit: int = 100,
) -> Sequence[AuditEvent]:
    """Return audit events matching the filters, newest first."""
    if limit <= 0 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000")

    stmt = select(AuditEvent)
    if event_type is not None:
        stmt = stmt.where(AuditEvent.type == event_type)
    if correlation_id is not None:
        stmt = stmt.where(AuditEvent.correlation_id == correlation_id)
    if since is not None:
        stmt = stmt.where(AuditEvent.occurred_at >= since)

    stmt = stmt.order_by(AuditEvent.occurred_at.desc()).limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()
