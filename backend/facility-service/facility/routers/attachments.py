"""Image attachments: presign, browser upload to S3, finalize, download (FR-6.4)."""

import asyncio
from datetime import timedelta
from typing import Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from facility.database import get_db
from facility.dependencies import current_user
from facility.enums import (
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS,
    AttachmentStatus,
    AttachmentType,
    IncidentEventType,
    NoteVisibility,
)
from facility.errors import fail
from facility.models import Attachment, IncidentNote, User, utcnow
from facility.notifications import commit
from facility.permissions import (
    can_staff,
    event,
    load_incident,
    relationship,
    require_nonfinal,
    require_permission,
)
from facility.storage import get_storage, signature_matches

router = APIRouter(tags=["attachments"])
Db = Annotated[AsyncSession, Depends(get_db)]
Actor = Annotated[User, Depends(current_user)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PresignRequest(Input):
    content_type: AttachmentType
    size_bytes: int = Field(ge=1, le=MAX_ATTACHMENT_BYTES)
    note_id: UUID | None = None


class FinalizeRequest(Input):
    attachment_id: UUID


def output(
    item: Attachment, visibility: NoteVisibility, hide: UUID | None = None
) -> dict[str, Any]:
    return {
        "id": str(item.id),
        "incident_id": str(item.incident_id),
        "note_id": str(item.note_id) if item.note_id else None,
        "uploaded_by": None if item.uploaded_by == hide else str(item.uploaded_by),
        "content_type": item.content_type,
        "size_bytes": item.size_bytes,
        "status": item.status,
        "visibility": visibility,
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "href": f"/api/v1/attachments/{item.id}",
    }


async def _note(db: AsyncSession, incident_id: UUID, note_id: UUID | None) -> IncidentNote | None:
    if note_id is None:
        return None
    note = await db.get(IncidentNote, note_id)
    if note is None or note.incident_id != incident_id:
        raise HTTPException(422, "The note does not belong to this incident")
    return note


@router.post("/incidents/{incident_id}/attachments/presign", status_code=201)
async def presign(incident_id: UUID, payload: PresignRequest, db: Db, user: Actor) -> dict:
    """Reserve a slot and return a presigned POST capped at 5 MB."""
    storage = get_storage()
    incident = await load_incident(db, user, incident_id, lock=True)
    staff = can_staff(user, incident)
    require_permission(staff or incident.created_by == user.id)
    require_nonfinal(incident)
    note = await _note(db, incident.id, payload.note_id)
    if note is not None:
        require_permission(note.visibility == NoteVisibility.PUBLIC or staff)
        if note.hidden_at is not None:
            raise HTTPException(409, "Hidden notes cannot take attachments")
    used = await db.scalar(
        select(func.count(Attachment.id)).where(
            Attachment.incident_id == incident.id,
            Attachment.status.in_([AttachmentStatus.PENDING, AttachmentStatus.ACCEPTED]),
        )
    )
    if used >= MAX_ATTACHMENTS:
        raise fail(409, "attachment_limit", f"At most {MAX_ATTACHMENTS} images per incident")
    item_id = uuid4()
    item = Attachment(
        id=item_id,
        incident_id=incident.id,
        note_id=payload.note_id,
        uploaded_by=user.id,
        s3_key=f"incidents/{incident.id}/{item_id}",
        content_type=payload.content_type,
        size_bytes=payload.size_bytes,
        status=AttachmentStatus.PENDING,
    )
    db.add(item)
    upload = await asyncio.to_thread(storage.presign_post, item.s3_key, payload.content_type)
    await db.commit()
    return {"attachment_id": str(item.id), "upload": upload, "expires_in": 300}


@router.post("/incidents/{incident_id}/attachments")
async def finalize(incident_id: UUID, payload: FinalizeRequest, db: Db, user: Actor) -> dict:
    """Verify the uploaded object's size and signature, then accept or reject it."""
    storage = get_storage()
    incident = await load_incident(db, user, incident_id, lock=True)
    require_nonfinal(incident)
    item = await db.scalar(
        select(Attachment)
        .where(Attachment.id == payload.attachment_id, Attachment.incident_id == incident.id)
        .with_for_update()
    )
    if item is None or item.uploaded_by != user.id:
        raise fail(404, "not_found", "Attachment not found")
    if item.status != AttachmentStatus.PENDING:
        raise fail(409, "conflict", "Attachment was already processed")
    if item.created_at < utcnow() - timedelta(hours=1):
        # The cleanup job owns pending uploads after an hour.
        raise fail(409, "upload_expired", "This upload expired; attach the image again")
    size = await asyncio.to_thread(storage.size, item.s3_key)
    if size is None:
        # Not uploaded yet: keep the row pending so the client can retry; the
        # cleanup job removes abandoned rows after an hour.
        raise fail(409, "upload_incomplete", "The file has not finished uploading; try again")
    head = b""
    if size is not None and 1 <= size <= MAX_ATTACHMENT_BYTES:
        head = await asyncio.to_thread(storage.head_bytes, item.s3_key, 32)
    if size is None or not signature_matches(item.content_type, head):
        item.status = AttachmentStatus.REJECTED
        if size is not None:
            await asyncio.to_thread(storage.delete, item.s3_key)
        await db.commit()
        raise fail(422, "attachment_invalid", "The file is missing or is not the declared image")
    item.size_bytes = size
    item.status = AttachmentStatus.ACCEPTED
    note = await _note(db, incident.id, item.note_id)
    visibility = note.visibility if note is not None else NoteVisibility.PUBLIC
    event(
        db,
        incident,
        user,
        IncidentEventType.ATTACHMENT_ADDED,
        {
            "attachment_id": str(item.id),
            "note_id": str(item.note_id) if item.note_id else None,
            "visibility": visibility,
        },
    )
    await commit(db)
    return output(item, visibility)


@router.get("/incidents/{incident_id}/attachments")
async def list_attachments(incident_id: UUID, db: Db, user: Actor) -> dict:
    """Accepted images the caller may see; internal-note and hidden-note images are filtered."""
    incident = await load_incident(db, user, incident_id)
    rel = await relationship(db, user, incident)
    staff = rel.staff_read
    hide = incident.created_by if rel.follower_only else None
    rows = (
        await db.execute(
            select(Attachment, IncidentNote.visibility, IncidentNote.hidden_at)
            .outerjoin(IncidentNote, IncidentNote.id == Attachment.note_id)
            .where(
                Attachment.incident_id == incident.id,
                Attachment.status == AttachmentStatus.ACCEPTED,
            )
            .order_by(Attachment.created_at, Attachment.id)
        )
    ).all()
    items = []
    for item, visibility, hidden_at in rows:
        visibility = visibility or NoteVisibility.PUBLIC
        if hidden_at is not None or (visibility == NoteVisibility.INTERNAL and not staff):
            continue
        items.append(output(item, visibility, hide))
    return {"items": items, "total": len(items)}


@router.get("/attachments/{attachment_id}")
async def download(attachment_id: UUID, db: Db, user: Actor) -> dict:
    """Return a 5-minute presigned GET after incident and note visibility checks."""
    item = await db.get(Attachment, attachment_id)
    if item is None or item.status != AttachmentStatus.ACCEPTED:
        raise fail(404, "not_found", "Attachment not found")
    try:
        incident = await load_incident(db, user, item.incident_id)
    except HTTPException:
        raise fail(404, "not_found", "Attachment not found") from None
    note = await db.get(IncidentNote, item.note_id) if item.note_id else None
    if note is not None:
        staff = (await relationship(db, user, incident)).staff_read
        if note.hidden_at is not None or (
            note.visibility == NoteVisibility.INTERNAL and not staff
        ):
            raise fail(404, "not_found", "Attachment not found")
    url = await asyncio.to_thread(get_storage().presign_get, item.s3_key)
    return {"url": url, "content_type": item.content_type, "expires_in": 300}
