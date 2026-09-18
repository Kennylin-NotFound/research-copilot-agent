from uuid import UUID
from typing import Literal
from pydantic import Field, BaseModel, ConfigDict, model_validator
from product.contracts import Contract


class SetupCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=10, max_length=128)


class LoginCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=1, max_length=128)


class Title(Contract):
    title: str = Field(min_length=1, max_length=120)


class ConversationUpdate(Contract):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    archived: bool | None = None


class SendMessage(Contract):
    content: str = Field(min_length=1, max_length=12000)
    client_message_id: UUID
    skill_id: Literal['paper-search','evidence-qa','paper-review','evidence-survey'] | None = None
    file_ids: list[UUID] | None = Field(default=None,max_length=10)
    model_id: Literal['default','fast'] = 'default'
    allow_network: bool = False

    @model_validator(mode="after")
    def network_permission_matches_skill(self):
        if self.skill_id == "paper-search" and not self.allow_network:
            raise ValueError("paper_search_requires_network_permission")
        if self.skill_id != "paper-search" and self.allow_network:
            raise ValueError("network_permission_only_for_paper_search")
        if self.skill_id == "paper-search" and self.file_ids is not None:
            raise ValueError("paper_search_does_not_accept_local_file_scope")
        return self


class FeedbackInput(Contract):
    rating: Literal[-1,1]
    note: str | None = Field(default=None,max_length=2000)
    artifact_version_id: UUID | None = None
