from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictStr


class RunInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    repository_id: Annotated[StrictStr, Field(min_length=1, max_length=100)]
    commit_sha: Annotated[StrictStr, Field(pattern=r'^[0-9a-f]{40}$')]


class Run(BaseModel):
    id: UUID
    repository_id: str
    ref: str
    commit_sha: str
    workflow_version: str
    state: Literal['queued', 'running', 'succeeded', 'failed', 'timed_out', 'canceled']
    created_at: datetime
    available_at: datetime
    cancel_requested_at: datetime | None
    completed_attempt_id: UUID | None


class Cursor(BaseModel):
    model_config = ConfigDict(extra='forbid')
    created_at: AwareDatetime
    id: UUID


class RunPage(BaseModel):
    items: list[Run]
    next_cursor: str | None


class ManualRetryReceipt(BaseModel):
    run_id: UUID
    previous_attempt_id: UUID
    requested_at: datetime


class Attempt(BaseModel):
    id: UUID
    attempt_no: int
    retry_kind: Literal['initial', 'infrastructure', 'manual']
    state: Literal['running', 'succeeded', 'failed', 'timed_out', 'canceled']
    started_at: datetime
    finished_at: datetime | None
    exit_code: int | None
    reason: str | None
    official: bool


class AttemptPage(BaseModel):
    items: list[Attempt]
    next_before: int | None
