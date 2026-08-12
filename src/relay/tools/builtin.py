"""Built-in tools - one per risk level, chosen to exercise every code path.

calculator      READ_ONLY, idempotent   - safe expression evaluation (AST
                whitelist, NOT eval() - see the sandboxing note below)
http_get        READ_ONLY, idempotent   - network read behind a domain
                allowlist (SSRF defense: the agent cannot be prompted into
                calling your cloud metadata endpoint)
write_file      WRITE, idempotent       - confined to a workspace dir with
                path-traversal protection
send_email      DESTRUCTIVE, NOT idempotent - requires human approval by
                default policy; delivery is a pluggable sink so tests and
                demos don't send anything real

Sandboxing note: these in-process guards (AST whitelist, allowlist,
path confinement) are defense for *well-typed* tools. Arbitrary
code-execution tools must run in an external sandbox (container/microVM);
that boundary is documented in ADR-0006 and LIMITATIONS.md.
"""

from __future__ import annotations

import ast
import asyncio
import math
import operator
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from relay.domain.types import RiskLevel
from relay.tools.base import Tool, ToolExecutionError

# --------------------------------------------------------------------------
# calculator: AST-whitelisted arithmetic. eval() would be an RCE hole.
# --------------------------------------------------------------------------

_BIN_OPS: dict[type, Callable[[Any, Any], Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: dict[type, Callable[[Any], Any]] = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_MAX_AST_NODES = 200
_MAX_POWER_EXPONENT = 100
_MAX_ABS_RESULT = 1e100
_MAX_FILE_CHARS = 1_000_000


def _checked_number(value: int | float) -> int | float:
    if isinstance(value, bool):
        raise ToolExecutionError("boolean values are not arithmetic operands", retryable=False)
    if isinstance(value, float) and not math.isfinite(value):
        raise ToolExecutionError("expression contains a non-finite value", retryable=False)
    if abs(value) > _MAX_ABS_RESULT:
        raise ToolExecutionError("expression value exceeds safe limits", retryable=False)
    return value


def _eval_node(node: ast.AST) -> int | float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return _checked_number(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and (
            abs(right) > _MAX_POWER_EXPONENT or abs(left) > _MAX_ABS_RESULT
        ):
            raise ToolExecutionError("power operation exceeds safe limits", retryable=False)
        result = _BIN_OPS[type(node.op)](left, right)
        return _checked_number(result)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _checked_number(_UNARY_OPS[type(node.op)](_eval_node(node.operand)))
    raise ToolExecutionError(
        f"unsupported expression element: {type(node).__name__}", retryable=False
    )


async def _calculator(args: dict[str, Any]) -> str:
    expression = str(args.get("expression", ""))
    if not expression:
        raise ToolExecutionError("missing 'expression' argument", retryable=False)
    if len(expression) > 1000:
        raise ToolExecutionError("expression too long", retryable=False)
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as e:
        raise ToolExecutionError(f"invalid expression: {e}", retryable=False) from e
    if sum(1 for _ in ast.walk(tree)) > _MAX_AST_NODES:
        raise ToolExecutionError("expression is too complex", retryable=False)
    try:
        return str(_eval_node(tree))
    except (ArithmeticError, OverflowError) as e:
        raise ToolExecutionError(f"invalid arithmetic: {e}", retryable=False) from e


calculator = Tool(
    name="calculator",
    description="Evaluate an arithmetic expression, e.g. '(2 + 3) * 4 / 7'.",
    parameters={
        "type": "object",
        "properties": {"expression": {"type": "string", "description": "Arithmetic expression"}},
        "required": ["expression"],
    },
    risk=RiskLevel.READ_ONLY,
    idempotent=True,
    handler=_calculator,
)

# --------------------------------------------------------------------------
# http_get: network read with SSRF defense via domain allowlist.
# --------------------------------------------------------------------------


def make_http_get(allowed_domains: tuple[str, ...], timeout_s: float = 10.0) -> Tool:
    async def _http_get(args: dict[str, Any]) -> str:
        url = str(args.get("url", ""))
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ToolExecutionError(f"unsupported scheme: {parsed.scheme!r}", retryable=False)
        host = parsed.hostname or ""
        if not any(host == d or host.endswith("." + d) for d in allowed_domains):
            raise ToolExecutionError(
                f"domain {host!r} is not in the allowlist {allowed_domains}", retryable=False
            )
        try:
            import httpx
        except ImportError as e:  # pragma: no cover
            raise ToolExecutionError("httpx not installed", retryable=False) from e
        try:
            async with httpx.AsyncClient(
                timeout=timeout_s, follow_redirects=False  # redirects could escape allowlist
            ) as client:
                resp = await client.get(url)
        except httpx.TimeoutException as e:
            raise ToolExecutionError(f"timeout fetching {url}", retryable=True) from e
        except httpx.HTTPError as e:
            raise ToolExecutionError(f"network error: {e}", retryable=True) from e
        if resp.status_code >= 500:
            raise ToolExecutionError(f"upstream {resp.status_code}", retryable=True)
        body = resp.text[:20_000]  # cap: tool output goes into model context
        return f"HTTP {resp.status_code}\n{body}"

    return Tool(
        name="http_get",
        description="Fetch a URL (GET). Only allowlisted domains are permitted.",
        parameters={
            "type": "object",
            "properties": {"url": {"type": "string"}},
            "required": ["url"],
        },
        risk=RiskLevel.READ_ONLY,
        idempotent=True,
        timeout_s=timeout_s + 5,
        handler=_http_get,
    )


# --------------------------------------------------------------------------
# write_file: confined to a workspace root; path traversal is rejected.
# --------------------------------------------------------------------------


def make_write_file(workspace: Path) -> Tool:
    async def _write_file(args: dict[str, Any]) -> str:
        rel = str(args.get("path", ""))
        content = str(args.get("content", ""))
        if not rel:
            raise ToolExecutionError("missing 'path'", retryable=False)
        if len(content) > _MAX_FILE_CHARS:
            raise ToolExecutionError(
                f"content exceeds {_MAX_FILE_CHARS} character limit", retryable=False
            )
        target = (workspace / rel).resolve()
        if not target.is_relative_to(workspace.resolve()):
            raise ToolExecutionError(
                f"path {rel!r} escapes the workspace - rejected", retryable=False
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return f"wrote {len(content)} bytes to {target.relative_to(workspace.resolve())}"

    return Tool(
        name="write_file",
        description="Write a text file inside the run workspace.",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path inside workspace"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
        risk=RiskLevel.WRITE,
        idempotent=True,  # same path+content -> same result
        handler=_write_file,
    )


# --------------------------------------------------------------------------
# send_email: DESTRUCTIVE + non-idempotent -> human approval by default,
# and crash recovery will NEVER blindly re-send it.
# --------------------------------------------------------------------------

EmailSink = Callable[[str, str, str], Awaitable[None]]


async def _default_sink(to: str, subject: str, body: str) -> None:
    # Demo sink: pretend to deliver. Swap for SES/SMTP in production.
    await asyncio.sleep(0)


def make_send_email(sink: EmailSink = _default_sink) -> Tool:
    async def _send_email(args: dict[str, Any]) -> str:
        to = str(args.get("to", ""))
        subject = str(args.get("subject", ""))
        body = str(args.get("body", ""))
        if not to or "@" not in to:
            raise ToolExecutionError(f"invalid recipient: {to!r}", retryable=False)
        await sink(to, subject, body)
        return f"email sent to {to} (subject: {subject!r})"

    return Tool(
        name="send_email",
        description="Send an email. Irreversible - requires human approval.",
        parameters={
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["to", "subject", "body"],
        },
        risk=RiskLevel.DESTRUCTIVE,
        idempotent=False,
        handler=_send_email,
    )
