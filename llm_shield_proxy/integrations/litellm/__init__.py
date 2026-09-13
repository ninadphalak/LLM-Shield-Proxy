"""LiteLLM host adapters.

Two ways to run LLM Shield under LiteLLM, in one place so it is obvious what this
is:

- ``guardrail`` -- ``LLMShieldProxyGuardrail``, a ``CustomGuardrail`` LiteLLM loads
  by dotted path from its own config. Reversible redaction and restoration, no
  change to LiteLLM's source.
- ``examples/integrations/litellm`` -- a shim for LiteLLM's built-in
  ``generic_guardrail_api``, which reaches the Shield's existing guard API without
  this package being imported by LiteLLM at all.

This module deliberately imports nothing, including its own children. Importing
``llm_shield_proxy.integrations.litellm`` must not pull LiteLLM in: the host is
present only in an environment that already has it, and
``tests/integrations/litellm/test_adapter.py`` asserts the host is unreachable
from the base install. Reference the guardrail by full dotted path:

    llm_shield_proxy.integrations.litellm.guardrail.LLMShieldProxyGuardrail
"""
