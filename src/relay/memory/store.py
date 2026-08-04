"""Long-term memory: how an agent "gets better" across runs.

LLMs are stateless - they do not learn from past runs by themselves.
Improvement across runs has to be engineered, and Relay does it with a
three-tier memory hierarchy:

  working memory   the transcript of the current run (rebuilt by the fold)
  episodic memory  the immutable event log of every past run (replayable)
  long-term memory THIS module: distilled lessons from completed runs,
                   retrieved by relevance and injected into future prompts

Retrieval here is deliberately simple keyword overlap (TF-style scoring).
It is correct, dependency-free, and easy to reason about; the production
upgrade path - embeddings + a vector index behind this same protocol -
is documented in LIMITATIONS.md. The protocol is the point: swapping the
retriever must not touch the engine.

Injection is auditable by design: retrieved lessons are appended to the
system prompt BEFORE RunCreated is emitted, so the event log records
exactly what memories influenced the run.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol

_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "a an and are as at be by for from has have how i in is it of on or that "
    "the this to was what when where which with you".split()
)


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS}


@dataclass(frozen=True)
class MemoryEntry:
    goal: str
    summary: str
    lessons: str  # "what to do differently next time"
    tags: tuple[str, ...] = ()
    entry_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def render(self) -> str:
        return f"- Past task: {self.goal}\n  Outcome: {self.summary}\n  Lesson: {self.lessons}"


class MemoryStore(Protocol):
    async def add(self, entry: MemoryEntry) -> None: ...

    async def search(self, query: str, *, limit: int = 3) -> list[MemoryEntry]: ...


class InMemoryMemoryStore:
    """Keyword-overlap retrieval. Score = |query tokens ∩ entry tokens|,
    normalized by entry length so short, precise lessons rank above
    rambling ones."""

    def __init__(self) -> None:
        self._entries: list[MemoryEntry] = []
        self._lock = asyncio.Lock()

    async def add(self, entry: MemoryEntry) -> None:
        async with self._lock:
            self._entries.append(entry)

    async def search(self, query: str, *, limit: int = 3) -> list[MemoryEntry]:
        q = _tokens(query)
        if not q:
            return []
        scored: list[tuple[float, MemoryEntry]] = []
        async with self._lock:
            entries = list(self._entries)
        for e in entries:
            e_tokens = _tokens(f"{e.goal} {e.summary} {e.lessons} {' '.join(e.tags)}")
            overlap = len(q & e_tokens)
            if overlap == 0:
                continue
            scored.append((overlap / (1 + len(e_tokens)) ** 0.5, e))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [e for _, e in scored[:limit]]


class JsonlMemoryStore(InMemoryMemoryStore):
    """File-backed variant: appends every entry to a JSONL file and reloads
    on startup. Durable enough for a single node; use Postgres/pgvector
    for multi-node production (LIMITATIONS.md)."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self._path = path
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    data = json.loads(line)
                    data["tags"] = tuple(data.get("tags", ()))
                    self._entries.append(MemoryEntry(**data))

    async def add(self, entry: MemoryEntry) -> None:
        await super().add(entry)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a") as f:
            record = asdict(entry)
            record["tags"] = list(entry.tags)
            f.write(json.dumps(record) + "\n")


def render_memory_block(entries: list[MemoryEntry]) -> str:
    """The block injected into a run's system prompt."""
    if not entries:
        return ""
    lines = "\n".join(e.render() for e in entries)
    return (
        "\n\n<relevant_experience>\n"
        "Lessons from similar past runs (use them, do not repeat mistakes):\n"
        f"{lines}\n"
        "</relevant_experience>"
    )
