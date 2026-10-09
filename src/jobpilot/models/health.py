"""Output schema for the trivial health-check prompt (prompts/system/ping_v1.md)."""

from pydantic import BaseModel


class PingResult(BaseModel):
    """The model's answer to the health-check ping."""

    ok: bool
    echo: str
