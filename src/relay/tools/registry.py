"""Tool registry: name -> Tool, with per-run scoping.

A run may declare `allowed_tools`; the registry view handed to that run's
loop contains only those. The model physically cannot call a tool it was
not granted - allowlisting happens in the runtime, not the prompt.
"""

from __future__ import annotations

from collections.abc import Iterable

from relay.llm.base import ToolDef
from relay.tools.base import Tool


class UnknownToolError(KeyError):
    pass


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise UnknownToolError(name) from None

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def scoped(self, allowed: tuple[str, ...]) -> ToolRegistry:
        """A restricted view for one run. Empty tuple = everything."""
        if not allowed:
            return self
        return ToolRegistry(t for n, t in self._tools.items() if n in allowed)

    def tool_defs(self) -> list[ToolDef]:
        return [t.to_def() for t in self._tools.values()]
