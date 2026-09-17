from uuid import UUID
from typing import Literal
from pydantic import Field, BaseModel, ConfigDict
from product.contracts import Contract


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=10, max_length=128)


class Title(Contract):
    title: str = Field(min_length=1, max_length=120)


class ConversationUpdate(Contract):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    archived: bool | None = None


class SendMessage(Contract):
    content: str = Field(min_length=1, max_length=12000)
    client_message_id: UUID
    skill_id: Literal['evidence-qa'] | None = None
    file_ids: list[UUID] | None = Field(default=None,max_length=10)
