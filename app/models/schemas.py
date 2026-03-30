from typing import List, Literal

from pydantic import BaseModel, Field


class Attachment(BaseModel):
    name: str
    mime_type: str
    data_url: str

class Message(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str
    attachments: List[Attachment] = Field(default_factory=list)


class ChatOptions(BaseModel):
    web_research_mode: Literal["auto", "force", "off"] = "auto"


class ChatRequest(BaseModel):
    messages: List[Message]
    options: ChatOptions = Field(default_factory=ChatOptions)


class WebSource(BaseModel):
    title: str
    url: str
    domain: str


class PipelineMetadata(BaseModel):
    route: str
    route_label: str
    route_reason: str
    validation_status: Literal["validated", "revised", "needs_clarification", "skipped"]
    used_vision: bool = False
    used_web_validation: bool = False
    web_validation_status: Literal["used", "skipped", "unavailable"] = "skipped"
    web_sources: List[WebSource] = Field(default_factory=list)
    correction_attempted: bool = False
    specialist_attempts: int = 1
    synthesis_status: Literal["used", "skipped"] = "skipped"
    stages: List[str] = Field(default_factory=list)


class ChatResponse(BaseModel):
    reply: str
    metadata: PipelineMetadata | None = None