"""Referral workflow routes."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, Response, UploadFile
from fastapi.responses import Response as RawResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_staff, get_session, request_uuid, require_staff
from app.core.auth import StaffPrincipal
from app.core.config import Settings
from app.core.errors import problem
from app.core.logging import log_event
from app.schemas.referrals import (
    ApproveRequest,
    ReferralDetail,
    ReferralPage,
    ReferralPatch,
    ReferralStatusName,
    ReferralStatusOut,
    ReferralSummary,
    RejectRequest,
    RetryRequest,
)
from app.services.files import FileRejected, attestation_accepted, inspect_upload
from app.services.intake import create_referral
from app.services.review import (
    approve_referral,
    list_referrals,
    patch_referral,
    read_document,
    read_referral,
    read_status,
    reject_referral,
    retry_referral,
)

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_staff)])

_FILE_CODES = {
    "file_too_large": "FILE_TOO_LARGE",
    "file_empty": "EMPTY_FILE",
    "unsupported_media_type": "UNSUPPORTED_FILE_TYPE",
    "file_corrupt": "UNREADABLE_PDF",
    "pdf_encrypted": "ENCRYPTED_PDF",
    "pdf_xfa_unsupported": "UNREADABLE_PDF",
    "too_many_pages": "TOO_MANY_PAGES",
}


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _reject_file(reason: str, size: int) -> NoReturn:
    log_event(logging.WARNING, "referral.upload.rejected", reason=reason, size_bytes=size)
    raise problem(_FILE_CODES[reason])


@router.get("/referrals", response_model=ReferralPage)
def list_referral_page(
    session: Annotated[Session, Depends(get_session)],
    status: Annotated[list[ReferralStatusName] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ReferralPage:
    return list_referrals(
        session, statuses=list(status) if status else None, limit=limit, offset=offset
    )


@router.post("/referrals", status_code=202, response_model=ReferralSummary)
def upload_referral(
    request: Request,
    response: Response,
    session: Annotated[Session, Depends(get_session)],
    staff: Annotated[StaffPrincipal, Depends(get_current_staff)],
    file: Annotated[UploadFile, File()],
    attestation: Annotated[str, Form()],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ReferralSummary:
    settings = _settings(request)
    if request.headers.get("content-length") is None:
        raise problem("LENGTH_REQUIRED")
    payload = file.file.read(settings.max_upload_bytes + 1)
    file.file.close()
    try:
        accepted = inspect_upload(
            payload,
            file.filename,
            max_bytes=settings.max_upload_bytes,
            max_pages=settings.max_pdf_pages,
        )
    except FileRejected as exc:
        _reject_file(exc.reason, len(payload))
    if not attestation_accepted(attestation):
        log_event(
            logging.WARNING,
            "referral.upload.rejected",
            reason="attestation_required",
            size_bytes=len(payload),
        )
        raise problem("VALIDATION_ERROR")
    key: uuid.UUID | None = None
    if idempotency_key:
        try:
            key = uuid.UUID(idempotency_key)
        except ValueError:
            raise problem("VALIDATION_ERROR") from None
    result = create_referral(
        session,
        settings=settings,
        storage=request.app.state.storage,
        runner=request.app.state.runner,
        clock=request.app.state.clock,
        staff_sub=staff.sub,
        request_id=request_uuid(request),
        accepted=accepted,
        body=payload,
        idempotency_key=key,
    )
    response.headers["Location"] = f"/api/v1/referrals/{result.referral_id}"
    return result.summary


@router.get("/referrals/{id}/status", response_model=ReferralStatusOut)
def poll_referral(
    id: uuid.UUID,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> ReferralStatusOut:
    return read_status(session, id, request.app.state.runner)


@router.get("/referrals/{id}/document")
def stream_document(
    id: uuid.UUID,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    staff: Annotated[StaffPrincipal, Depends(get_current_staff)],
) -> RawResponse:
    body, media_type, filename = read_document(
        session,
        id,
        storage=request.app.state.storage,
        staff_sub=staff.sub,
        request_id=request_uuid(request),
    )
    return RawResponse(
        content=body,
        media_type=media_type,
        headers={
            "Content-Disposition": f'inline; filename="{filename}"',
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/referrals/{id}", response_model=ReferralDetail)
def read_one_referral(
    id: uuid.UUID,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    staff: Annotated[StaffPrincipal, Depends(get_current_staff)],
) -> ReferralDetail:
    return read_referral(
        session,
        id,
        settings=_settings(request),
        staff_sub=staff.sub,
        request_id=request_uuid(request),
        clock=request.app.state.clock,
    )


@router.patch("/referrals/{id}", response_model=ReferralDetail)
def correct_referral(
    id: uuid.UUID,
    body: ReferralPatch,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    staff: Annotated[StaffPrincipal, Depends(get_current_staff)],
) -> ReferralDetail:
    return patch_referral(
        session,
        id,
        body,
        settings=_settings(request),
        staff_sub=staff.sub,
        request_id=request_uuid(request),
        clock=request.app.state.clock,
    )


@router.post("/referrals/{id}/approve", response_model=ReferralDetail)
def approve_one(
    id: uuid.UUID,
    body: ApproveRequest,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    staff: Annotated[StaffPrincipal, Depends(get_current_staff)],
) -> ReferralDetail:
    return approve_referral(
        session,
        id,
        body,
        settings=_settings(request),
        staff_sub=staff.sub,
        request_id=request_uuid(request),
        clock=request.app.state.clock,
    )


@router.post("/referrals/{id}/reject", response_model=ReferralDetail)
def reject_one(
    id: uuid.UUID,
    body: RejectRequest,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    staff: Annotated[StaffPrincipal, Depends(get_current_staff)],
) -> ReferralDetail:
    return reject_referral(
        session,
        id,
        body,
        settings=_settings(request),
        staff_sub=staff.sub,
        request_id=request_uuid(request),
        clock=request.app.state.clock,
    )


@router.post("/referrals/{id}/retry", status_code=202, response_model=ReferralDetail)
def retry_one(
    id: uuid.UUID,
    body: RetryRequest,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
    staff: Annotated[StaffPrincipal, Depends(get_current_staff)],
) -> ReferralDetail:
    return retry_referral(
        session,
        id,
        body.version,
        settings=_settings(request),
        runner=request.app.state.runner,
        staff_sub=staff.sub,
        request_id=request_uuid(request),
        clock=request.app.state.clock,
    )
