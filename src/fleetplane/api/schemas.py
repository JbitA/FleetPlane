from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from fleetplane.domain.enums import CommandKind


class CommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: CommandKind
    payload: dict[str, Any] = Field(default_factory=dict)
    ttl_seconds: int = Field(default=30, ge=5, le=300)
