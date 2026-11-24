"""Spirit data and registry. The engine knows spirits only through these types."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

SUMMON_TOOL = "summon_spirit"


@dataclass(frozen=True)
class ToolDef:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], dict]
    # Maps a tool result dict to the fields of an `evidence` UI event (without "type").
    to_evidence: Callable[[dict], dict] | None = None

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description,
                "input_schema": self.input_schema}


@dataclass(frozen=True)
class Spirit:
    id: str
    persona_prompt: str
    voice_id: str
    tools: tuple = ()
    appearance_path: str | None = None


class Registry:
    def __init__(self):
        self._spirits: dict[str, Spirit] = {}

    def register(self, spirit: Spirit) -> None:
        self._spirits[spirit.id] = spirit

    def get(self, spirit_id: str) -> Spirit:
        try:
            return self._spirits[spirit_id]
        except KeyError:
            raise KeyError(f"unknown spirit: {spirit_id}") from None

    def ids(self) -> list[str]:
        return list(self._spirits)

    def __contains__(self, spirit_id: str) -> bool:
        return spirit_id in self._spirits


DEFAULT_REGISTRY = Registry()


def register(spirit: Spirit) -> None:
    DEFAULT_REGISTRY.register(spirit)


def get(spirit_id: str) -> Spirit:
    return DEFAULT_REGISTRY.get(spirit_id)


def ids() -> list[str]:
    return DEFAULT_REGISTRY.ids()


def summon_tool_def(registry: Registry) -> ToolDef:
    """Generic tool letting the model summon another spirit. The session handles the call."""
    return ToolDef(
        name=SUMMON_TOOL,
        description="Summon a different spirit to continue the conversation.",
        input_schema={"type": "object",
                      "properties": {"spirit": {"type": "string", "enum": registry.ids()}},
                      "required": ["spirit"]},
        handler=lambda args: {"summoning": args.get("spirit")},
    )
