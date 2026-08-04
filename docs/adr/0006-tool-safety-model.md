# ADR-0006: Declared-risk tool safety model; in-process guards vs external sandboxes

Status: accepted

## Context
The model chooses tool calls; tool authors write handlers; neither can be fully trusted. We need a safety model that is enforceable by the runtime and honest about its boundary.

## Decision
Three enforcement layers, all in the runtime:
1. **Capability scoping.** Per-run `allowed_tools` enforced by registry scoping — a tool outside the allowlist is invisible to the model and unexecutable even if hallucinated.
2. **Declared risk + policy.** Tools declare `read_only`/`write`/`destructive`; a deny-by-default policy engine maps risk to allow/approve/deny with per-tool, auditable overrides.
3. **Defensive tool implementation.** Builtins demonstrate the patterns: AST-whitelisted arithmetic instead of `eval`; SSRF defense via domain allowlist with redirects disabled; path-traversal-proof file writes; output caps (tool output enters model context and therefore cost).

**The honest boundary:** these in-process guards protect against *misuse of well-typed tools*. They do not contain arbitrary code execution. A `run_python` or `bash` tool MUST execute in an external sandbox (container with no network + seccomp, gVisor, or Firecracker microVM) with the runtime treating it as just another async handler with a timeout. That integration is out of scope here and explicitly listed in LIMITATIONS.md — claiming in-process "sandboxing" would be security theater.

## Consequences
Risk metadata is self-declared by tool authors — a code-review responsibility, mitigated by deny-by-default for anything undeclared (unknown risk → require approval; unknown tool → deny). The model's attack surface (prompt injection steering tool calls) is bounded by allowlists + policy + human gates on destructive actions: injection can waste budget, but the budget circuit breaker caps that too.
