"""Caller identity. The username is the placeholder ``staff`` and email is omitted."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.api.deps import StaffPrincipal, get_current_staff, require_staff

router = APIRouter(prefix="/api/v1", dependencies=[Depends(require_staff)])


class MeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    username: Literal["staff"]


@router.get("/me", response_model=MeResponse)
def read_me(staff: Annotated[StaffPrincipal, Depends(get_current_staff)]) -> MeResponse:
    return MeResponse(user_id=uuid.UUID(staff.sub), username="staff")
