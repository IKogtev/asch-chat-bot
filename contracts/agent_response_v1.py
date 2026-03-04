from pydantic import BaseModel, Field, ConfigDict
from typing import List, Literal, Optional

class Attachment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1)
    title: str = Field(min_length=1)
    mime: Optional[str] = None

class AgentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract_version: Literal["1.0"] = "1.0"
    answer: str = Field(min_length=1)
    attachments: List[Attachment] = Field(default_factory=list)