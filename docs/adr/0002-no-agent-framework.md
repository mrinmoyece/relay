# ADR-0002: No agent framework (LangChain / CrewAI / AutoGen)

Status: accepted

## Context
Agent frameworks provide prompt templates, tool abstractions, and pre-built loops. Using one is the default choice in 2026; not using one needs justification.

## Decision
Build the loop, state management, and tool dispatch directly on provider SDKs, behind our own thin protocols.

## Reasoning
1. **The framework hides exactly the layer this project exists to own.** Durable execution, optimistic concurrency, idempotency-aware recovery, budget circuit breakers, and policy gates all live *underneath* a framework's loop. To implement them we would have to fight or fork the framework's execution model.
2. **Failure-mode control.** When a tool times out or a provider rate-limits, our executor decides retry/backoff/surface-to-model per an explicit contract. Framework retry behavior is configurable at best, opaque at worst — and debugging an agent means reading the loop.
3. **Dependency surface.** Frameworks churn fast and pull large dependency trees into a service whose core needs ~4 libraries. The event log is a long-lived schema; coupling it to a fast-moving framework's abstractions is a liability.
4. **What we give up:** prebuilt integrations (vector stores, loaders, hundreds of tools) and community patterns. Acceptable: this runtime's tool contract is small, and integrations plug in at the `Tool`/`LLMProvider` seams where a framework adapter could even be hosted if needed.

## Consequences
More code we own (~2k LOC of runtime), fully testable and explainable. New tool integrations cost slightly more than `pip install`. The team must understand agent loops — which, for this project, is the point.
