from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.services import note_contributions as service


class FakeResult:
    def __init__(self, *, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def fetchall(self):
        return list(self._rows)

    def scalar_one_or_none(self):
        return self._scalar


def make_row(**values):
    return SimpleNamespace(_mapping=values)


class FakeDb:
    def __init__(self, *results: FakeResult):
        self._results = list(results)
        self.execute = AsyncMock(side_effect=self._execute)

    async def _execute(self, *_args, **_kwargs):
        if not self._results:
            raise AssertionError("Unexpected execute() call")
        return self._results.pop(0)


@pytest.mark.asyncio
async def test_list_note_contributions_uses_materialized_view_when_fresh() -> None:
    note_id = uuid4()
    workspace_id = uuid4()
    contributor_user_id = uuid4()
    latest_at = datetime.now(timezone.utc)
    db = FakeDb(
        FakeResult(
            rows=[
                make_row(
                    note_id=note_id,
                    workspace_id=workspace_id,
                    contributor_user_id=contributor_user_id,
                    contribution_count=3,
                    note_create_count=1,
                    note_update_count=2,
                    note_restore_count=0,
                    thinking_contribution_count=0,
                    vote_cast_count=0,
                    first_contribution_at=latest_at,
                    last_contribution_at=latest_at,
                    contributor_name="Ayo",
                    contributor_email="ayo@example.com",
                )
            ]
        ),
        FakeResult(scalar=latest_at),
    )

    contributions = await service.list_note_contributions(db, note_id)

    assert len(contributions) == 1
    assert contributions[0].contribution_count == 3
    assert contributions[0].contributor_name == "Ayo"
    assert db.execute.await_count == 2


@pytest.mark.asyncio
async def test_list_note_contributions_falls_back_to_raw_when_materialized_view_is_stale() -> None:
    note_id = uuid4()
    workspace_id = uuid4()
    contributor_user_id = uuid4()
    stale_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    fresh_at = datetime.now(timezone.utc)
    db = FakeDb(
        FakeResult(
            rows=[
                make_row(
                    note_id=note_id,
                    workspace_id=workspace_id,
                    contributor_user_id=contributor_user_id,
                    contribution_count=1,
                    note_create_count=1,
                    note_update_count=0,
                    note_restore_count=0,
                    thinking_contribution_count=0,
                    vote_cast_count=0,
                    first_contribution_at=stale_at,
                    last_contribution_at=stale_at,
                    contributor_name="Ayo",
                    contributor_email="ayo@example.com",
                )
            ]
        ),
        FakeResult(scalar=fresh_at),
        FakeResult(
            rows=[
                make_row(
                    note_id=note_id,
                    workspace_id=workspace_id,
                    contributor_user_id=contributor_user_id,
                    contribution_count=4,
                    note_create_count=1,
                    note_update_count=2,
                    note_restore_count=0,
                    thinking_contribution_count=1,
                    vote_cast_count=0,
                    first_contribution_at=stale_at,
                    last_contribution_at=fresh_at,
                    contributor_name="Ayo",
                    contributor_email="ayo@example.com",
                )
            ]
        ),
    )

    contributions = await service.list_note_contributions(db, note_id)

    assert len(contributions) == 1
    assert contributions[0].contribution_count == 4
    assert contributions[0].thinking_contribution_count == 1
    assert db.execute.await_count == 3


def test_build_note_contribution_breakdown_maps_fields() -> None:
    summary = service.NoteContributionSummary(
        note_id=uuid4(),
        workspace_id=uuid4(),
        contributor_user_id=uuid4(),
        contribution_count=6,
        note_create_count=1,
        note_update_count=3,
        note_restore_count=1,
        thinking_contribution_count=1,
        vote_cast_count=2,
        first_contribution_at=None,
        last_contribution_at=None,
    )

    breakdown = service.build_note_contribution_breakdown(summary)

    assert breakdown == {
        "note_created": 1,
        "note_updated": 3,
        "note_restored": 1,
        "thinking_contributions": 1,
        "votes_cast": 2,
    }
