"""Boundary tests for authorization, immutable versions and hidden projections."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException

from facility.models import Incident, IncidentNote, User
from facility.permissions import can_staff, check_match, touch
from facility.routers.incidents import note_projection


@pytest.mark.parametrize(
    ("roles", "assigned", "expected"),
    [
        ([], False, False),
        ([], True, False),
        (["engineer"], False, False),
        (["engineer"], True, True),
        (["admin"], False, True),
        (["admin"], True, True),
        (["admin", "engineer"], False, True),
        (["admin", "engineer"], True, True),
    ],
)
def test_staff_access_depends_on_incident_assignment(roles, assigned, expected):
    """A global engineer role alone never grants internal ticket access."""
    user = User(id=uuid4(), roles=roles)
    incident = Incident(assigned_to=user.id if assigned else uuid4())
    assert can_staff(user, incident) is expected


@pytest.mark.parametrize(
    "value", [None, "*", '"invalid"', "2026-10-21T01:00:00", "2026-10-21T01:00:01Z"]
)
def test_version_precondition_rejects_missing_invalid_and_stale(value):
    """Only an explicit timezone-aware version can authorize a mutation."""
    incident = Incident(updated_at=datetime(2026, 10, 21, 1, tzinfo=UTC))
    with pytest.raises(HTTPException) as error:
        check_match(incident, value)
    assert error.value.status_code == (428 if value is None else 409)


@pytest.mark.parametrize(
    "value",
    [
        "2026-10-21T01:00:00+00:00",
        '"2026-10-21T01:00:00Z"',
        '"2026-10-20T21:00:00-04:00"',
    ],
)
def test_version_precondition_accepts_same_instant(value):
    """Equivalent offsets match the stored UTC instant."""
    incident = Incident(updated_at=datetime(2026, 10, 21, 1, tzinfo=UTC))
    check_match(incident, value)


def test_touch_always_advances_clock_even_if_previous_version_is_in_future():
    """Optimistic versions cannot stay unchanged after an accepted write."""
    previous = datetime.now(UTC) + timedelta(days=1)
    incident = Incident(updated_at=previous)
    touch(incident)
    assert incident.updated_at > previous


def test_hidden_note_ordinary_projection_has_no_body_or_hiding_reason():
    """Audited hiding retains storage but every ordinary view is a tombstone."""
    note = IncidentNote(
        id=uuid4(),
        incident_id=uuid4(),
        author_id=uuid4(),
        visibility="public",
        body="private removed content",
        hide_reason="sensitive compliance reason",
        hidden_by=uuid4(),
        hidden_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    projected = note_projection(note)
    assert projected["hidden"] is True
    assert "body" not in projected
    assert "hide_reason" not in projected
    assert "private removed content" not in str(projected)
    assert "sensitive compliance reason" not in str(projected)


def test_visible_note_projection_preserves_plain_text_without_html_interpretation():
    """The API delivers text as JSON; clients render it as text, not markup."""
    note = IncidentNote(
        id=uuid4(),
        incident_id=uuid4(),
        author_id=uuid4(),
        visibility="public",
        body="<script>alert('text')</script>",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    projected = note_projection(note)
    assert projected["body"] == note.body
    assert projected["hidden"] is False
