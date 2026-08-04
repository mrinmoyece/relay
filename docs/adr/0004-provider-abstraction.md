# ADR-0004: Neutral provider interface; adapters own all vendor shapes

Status: accepted

## Context
Model vendors differ in wire format (Anthropic content blocks vs OpenAI messages vs Bedrock's per-model schemas), tool-call encoding, and usage reporting. The event log must stay stable for years; vendor APIs will not.

## Decision
The engine and the event log speak only Relay-neutral types (`ChatMessage`, `ToolCallSpec`, `Usage`, `ModelTurn`). Each provider is one adapter file that translates in both directions and computes USD cost. The provider contract includes:
- `Usage` with token counts AND `cost_usd` — budget enforcement is impossible without provider-computed cost.
- `ProviderError(retryable=bool)` — the adapter classifies vendor errors (rate limit/5xx/connection = retryable; auth/validation = not), so the engine's retry policy is vendor-agnostic.

Why not Bedrock/Azure/Gemini adapters now? They are hosting layers; adding them is mechanical (one file each, same tests) and adds no architectural information. The deterministic mock adapter, by contrast, is load-bearing: it is what makes agent behavior testable and the eval suite exact.

## Consequences
Swapping or adding vendors never touches domain/engine/store; replaying an old run's ledger is possible even after a vendor is gone. Cost: we maintain our own message format and a static price table per adapter (config-sourced pricing is the production upgrade, see LIMITATIONS.md). Lowest-common-denominator risk (vendor-specific features like prompt caching) is handled by adapter-level options, not by leaking vendor types upward.
