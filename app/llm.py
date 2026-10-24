"""Single gateway to the model: forced-JSON tool calls, usage tracking, hard spend limits."""
import threading

from . import config


class LLMUnavailable(RuntimeError):
    """Model could not be used (no key, API error, truncated output). Message is user-safe."""


class BudgetExceeded(RuntimeError):
    """Per-audit call or token budget reached."""


class UsageTracker:
    def __init__(self):
        self.lock = threading.Lock()
        self.by_agent: dict[str, dict] = {}
        self.calls = 0

    def total_tokens(self) -> int:
        return sum(r["input"] + r["cache_read"] + r["cache_write"] + r["output"] for r in self.by_agent.values())

    def add(self, agent, inp, out, cache_read, cache_write):
        with self.lock:
            r = self.by_agent.setdefault(agent, {"calls": 0, "input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
            r["calls"] += 1
            r["input"] += inp
            r["output"] += out
            r["cache_read"] += cache_read
            r["cache_write"] += cache_write
            self.calls += 1

    @staticmethod
    def cost_of(r) -> float:
        pi, po = config.PRICE_INPUT_PER_M, config.PRICE_OUTPUT_PER_M
        return (r["input"] * pi + r["cache_read"] * pi * 0.10 + r["cache_write"] * pi * 1.25 + r["output"] * po) / 1_000_000

    def summary(self) -> dict:
        agents = {a: {**r, "cost_usd": round(self.cost_of(r), 6)} for a, r in self.by_agent.items()}
        tot = {k: sum(r[k] for r in self.by_agent.values()) for k in ("calls", "input", "output", "cache_read", "cache_write")}
        tot["total_tokens"] = self.total_tokens()
        tot["cost_usd"] = round(sum(a["cost_usd"] for a in agents.values()), 6)
        return {"by_agent": agents, "total": tot, "model": config.AI_MODEL,
                "price_input_per_m": config.PRICE_INPUT_PER_M, "price_output_per_m": config.PRICE_OUTPUT_PER_M}


class LLM:
    def __init__(self):
        self.tracker = UsageTracker()
        self._client = None

    def _get_client(self):
        if not config.PORTKEY_API_KEY:
            raise LLMUnavailable("PORTKEY_API_KEY is not set, so the AI agents could not run. Technical checks still completed.")
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(api_key="unused", base_url=config.PORTKEY_BASE_URL, timeout=60.0, max_retries=2,
                                  default_headers={"x-portkey-api-key": config.PORTKEY_API_KEY, "x-portkey-provider": "openai"})
        return self._client

    def call_json(self, agent: str, system: str, user: str, schema: dict, max_tokens: int = 3000) -> dict:
        client = self._get_client()
        if self.tracker.calls >= config.MAX_LLM_CALLS:
            raise BudgetExceeded("Per-audit model-call limit reached.")
        if self.tracker.total_tokens() >= config.MAX_AUDIT_TOKENS:
            raise BudgetExceeded("Per-audit token budget reached.")
        import json
        import openai
        if config.EVAL_NONCE:  # evaluation only: defeats response caching so repeated runs are independent samples
            user += f"\n\n[evaluation run id: {config.EVAL_NONCE}]"
        try:
            resp = client.chat.completions.create(
                model=config.AI_MODEL, max_completion_tokens=max_tokens, reasoning_effort="none",
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                tools=[{"type": "function", "function": {"name": "submit", "description": "Submit the structured result.", "parameters": schema}}],
                tool_choice={"type": "function", "function": {"name": "submit"}},
            )
        except openai.AuthenticationError:
            raise LLMUnavailable("The model API key was rejected.")
        except openai.APIError as e:
            if "content_filter" in str(e):
                raise LLMUnavailable("The AI provider's safety filter blocked this page content, so the AI step could not run.")
            raise LLMUnavailable(f"The model request failed ({type(e).__name__}).")
        u = resp.usage
        cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        self.tracker.add(agent, (u.prompt_tokens or 0) - cached, u.completion_tokens or 0, cached, 0)
        choice = resp.choices[0]
        if choice.finish_reason == "length":
            raise LLMUnavailable("The model response was cut off.")
        calls = choice.message.tool_calls or []
        if not calls:
            raise LLMUnavailable("The model returned no structured result.")
        try:
            return json.loads(calls[0].function.arguments)
        except ValueError:
            raise LLMUnavailable("The model returned malformed JSON.")
