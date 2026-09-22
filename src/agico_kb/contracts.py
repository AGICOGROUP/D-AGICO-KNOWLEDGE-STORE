from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PrepareUpload(Contract):
    filename: str = Field(min_length=1, max_length=240)
    size: int = Field(gt=0)
    sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @field_validator("filename")
    @classmethod
    def safe_name(cls, value):
        if (
            value in {".", ".."}
            or any(c in value for c in "/\\:")
            or any(ord(c) < 32 for c in value)
        ):
            raise ValueError("文件名不能包含路径或控制字符")
        if value.startswith(("~$", ".~")):
            # Office/编辑器临时锁文件（文档被打开时产生），不是真文档，解析必失败。
            raise ValueError("这是 Office 临时锁文件，不是文档本身；请关闭文档后重新选择对应文件。")
        return value


class Submission(Contract):
    upload_id: UUID
    organization_id: str = Field(min_length=1, max_length=64)
    category_id: str = "other"
    title: str = Field(min_length=1, max_length=300)
    visibility: Literal["department", "private"] = "department"
    document_id: UUID | None = None
    base_revision: int = Field(default=0, ge=0)
    source_version: str | None = Field(default=None, max_length=120)
    product: str | None = Field(default=None, max_length=300)
    model: str | None = Field(default=None, max_length=300)
    project: str | None = Field(default=None, max_length=300)
    business_date: date | None = None


class Revision(Contract):
    expected_revision: int = Field(ge=0)


class Publish(Revision):
    accept_incomplete: bool = False
    category_id: str | None = Field(default=None, min_length=1, max_length=64)


class Grants(Revision):
    principal_ids: list[str] = Field(max_length=200)


class SearchRequest(Contract):
    query: str = Field(default="", max_length=500)
    mode: Literal["files", "content"] = "content"
    organization_id: str | None = None
    category_id: str | None = None
    model: str | None = Field(default=None, max_length=300)
    document_id: UUID | None = None
    business_date_from: date | None = None
    business_date_to: date | None = None
    limit: int = Field(default=5, ge=1, le=20)
    offset: int = Field(default=0, ge=0, le=1000)


class ReadRequest(Contract):
    version_id: UUID
    chunk_id: UUID | None = None
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=5, ge=1, le=20)
    max_chars: int = Field(default=12000, ge=1000, le=50000)


class ContextRequest(Contract):
    organization_id: str | None = None
    query: str = Field(default="", max_length=500)


class LinkRequest(Contract):
    child_version_id: UUID
    label: str = Field(min_length=1, max_length=100)


class ChunkEdit(Contract):
    """A reviewer's correction to one parsed knowledge chunk of a pending draft."""

    chunk_id: UUID
    text: str = Field(min_length=1, max_length=20000)
