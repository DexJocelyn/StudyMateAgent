from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Literal
from backend.config import TZ


def timestamp(value):
    dt = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise ValueError('时间必须带时区，例如 2026-10-23T22:00:00+08:00')
    if dt.second or dt.microsecond:
        raise ValueError('本版时间精确到分钟，秒必须为 00')
    return dt.astimezone(TZ)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class TaskInput(StrictModel):
    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default='', max_length=4000)
    assignee_id: str
    deadline: str
    estimated_minutes: int = Field(strict=True, gt=0, le=10080)
    status: Literal['todo', 'doing', 'paused', 'done'] = 'todo'

    @field_validator('title')
    @classmethod
    def title_not_blank(cls, value):
        if not value.strip():
            raise ValueError('任务名不能为空')
        return value.strip()

    @field_validator('deadline')
    @classmethod
    def valid_deadline(cls, value):
        return timestamp(value).isoformat()


class Window(StrictModel):
    start_at: str
    end_at: str

    @field_validator('start_at', 'end_at')
    @classmethod
    def valid_time(cls, value):
        return timestamp(value).isoformat()


class Block(Window):
    task_id: str
    assignee_id: str


class ChatRequest(StrictModel):
    project_id: str
    session_id: str
    member_id: str | None = None
    request_id: str = Field(min_length=1, max_length=100)
    message: str = Field(default='', max_length=20000)
    action: dict | None = None
