# Security policy

## Reporting

Report vulnerabilities privately via GitHub Security Advisories (or email the
maintainer). Please do not open public issues for exploitable problems.
Expect an acknowledgement within 72 hours.

## Threat model (summary)

Relay assumes three untrusted inputs:

1. **Model output** — tool calls are attacker-influenceable (prompt
   injection). Contained by per-run tool allowlists, deny-by-default policy,
   human approval on destructive actions, and budget circuit breakers.
2. **Tool arguments** — always validated by tools; guards include AST-only
   expression evaluation (no eval), SSRF domain allowlists with redirects
   disabled, and path-traversal confinement.
3. **Tool code** — executor isolates failures (timeouts, exception
   containment), but in-process guards do NOT contain arbitrary code
   execution. Code-execution tools require an external sandbox
   (container/microVM) — see ADR-0006.

## Known gaps (deliberate, documented)

- No authentication/authorization on the API — deploy behind your gateway or
  add OIDC before any real exposure (`docs/LIMITATIONS.md`).
- Risk levels are self-declared by tool authors; code review is the control.

## Secrets

Configuration is env-var only (`config.py`); never commit keys. The event
ledger stores prompts and tool outputs — treat the database with the same
sensitivity as the data your tools touch.
