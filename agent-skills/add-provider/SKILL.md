---
name: add-provider
description: Add a new LLM provider adapter (Bedrock, Azure, Gemini, OpenAI, etc.) to Relay behind the neutral LLMProvider protocol. Use whenever asked to support a new model vendor or hosting platform.
---

# Add an LLM provider to Relay

## Procedure

1. **One new file only**: `src/relay/llm/<vendor>.py`. Nothing in `domain/`,
   `engine/`, or `store/` may change — if it seems necessary, stop and flag it
   (see ADR-0004).
2. **Optional dependency**: guard the vendor SDK import in a try/except and add
   an extra to `pyproject.toml` (`relay-runtime[<vendor>]`), mirroring
   `llm/anthropic.py`.
3. **Implement `complete()`** translating both directions:
   - neutral `ChatMessage` transcript → vendor wire format. Handle all four
     roles; tool results must be correlated via `tool_call_id`; consecutive
     tool messages may need merging (see `_to_anthropic` for the pattern).
   - vendor response → `ModelTurn` (content, `ToolCallSpec` tuple, stop reason).
4. **Cost accounting is mandatory**: fill `Usage` with input/output tokens and
   computed `cost_usd` from a price table in the adapter. Budgets cannot work
   without it. Note config-sourced pricing as the upgrade path.
5. **Classify errors** into `ProviderError(retryable=...)`:
   retryable = rate limits, 5xx, timeouts, connection errors;
   non-retryable = auth, invalid request, content policy.
6. **Wire selection** in `api/app.py::build_manager` behind
   `RELAY_PROVIDER=<vendor>` and document the env vars in `.env.example`.
7. **Test the translation layer** pure (no network): given a transcript with
   system/user/assistant+tool_calls/tool messages, assert the exact wire
   payload; given a fake vendor response, assert the `ModelTurn`. Mock the SDK
   client for error-classification tests.

## Checklist

- [ ] vendor types appear ONLY in the new adapter file
- [ ] `Usage.cost_usd` computed; budget enforcement verified in a test
- [ ] error classification tested (retryable and non-retryable paths)
- [ ] optional-import guard + pyproject extra added
- [ ] `make lint && make test && make evals` pass
