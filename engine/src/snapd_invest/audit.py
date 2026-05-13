"""Audit log — append-only event recording.

Every important decision in the system records an `AuditEvent`. Reads are by
type, correlation_id, or time range.

Payloads are JSON-serialized for flexibility. Schema-discipline is the caller's
responsibility — `record_event` validates only that the payload is JSON-encodable.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from snapd_invest.models import AuditEvent, new_id

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from sqlalchemy.ext.asyncio import AsyncSession

    from snapd_invest.clock import Clock


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
        payload_json = json.dumps(payload, sort_keys=True)
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


MAX_AUDIT_LIMIT = 1000


async def list_events(
    session: AsyncSession,
    *,
    event_type: str | None = None,
    correlation_id: str | None = None,
    since: datetime | None = None,
    limit: int = 100,
) -> Sequence[AuditEvent]:
    """Return audit events matching the filters, newest first."""
    if limit <= 0 or limit > MAX_AUDIT_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_AUDIT_LIMIT}")

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
