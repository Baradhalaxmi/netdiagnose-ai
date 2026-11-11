"""Provider abstraction (ADR-8). The runner only needs ``generate``; swapping
providers or mocking in tests touches nothing else.

The response object is the provider SDK's message (for Anthropic, a
BetaMessage): the runner reads ``stop_reason``, ``content`` blocks
(``type``, and for tool calls ``id``/``name``/``input``), ``usage`` and
``model``.
"""

from __future__ import annotations

from typing import Any, Protocol

from backend.config import Settings

DEFAULT_MODEL = "claude-opus-5-5"


class LLMError(Exception):
    """Provider failure the runner turns into a rules fallback."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


class LLMClient(Protocol):
    provider: str
    model: str

    def generate(self, system: str, messages: list[dict], tools: list[dict]) -> Any: ...


class AnthropicLLM:
    """Claude via the Anthropic Messages API.

    * Thinking is always on for Claude Opus 5.5; depth is set by ``effort``.
    * ``fallbacks="default"``: if a safety classifier declines the request,
      the API retries it on Anthropic's recommended fallback model inside the
      same call. If the whole chain declines, stop_reason is "refusal" and the
      runner falls back to the rules report.
    * The system prompt and tool list are identical for every incident, so
      they are cached (prefix caching).
    """

    provider = "anthropic"

    def __init__(self, settings: Settings):
        import anthropic

        self._anthropic = anthropic
        # api_key=None lets the SDK resolve ANTHROPIC_API_KEY / an `ant auth login` profile.
        self.client = anthropic.Anthropic(api_key=settings.llm_api_key or None,
                                          timeout=120.0, max_retries=2)
        self.model = settings.llm_model or DEFAULT_MODEL
        self.effort = settings.agent_effort

    def generate(self, system: str, messages: list[dict], tools: list[dict]):
        a = self._anthropic
        try:
            return self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                tools=tools,
                messages=messages,
                output_config={"effort": self.effort},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except a.AuthenticationError as exc:
            raise LLMError("auth", "LLM API key missing or invalid") from exc
        except a.PermissionDeniedError as exc:
            raise LLMError("permission", exc.message) from exc
        except a.RateLimitError as exc:
            raise LLMError("rate_limited", exc.message) from exc
        except a.BadRequestError as exc:
            raise LLMError("bad_request", exc.message) from exc
        except a.APIStatusError as exc:
            raise LLMError("provider_error", f"HTTP {exc.status_code}") from exc
        except a.APIConnectionError as exc:
            raise LLMError("unreachable", str(exc)) from exc
