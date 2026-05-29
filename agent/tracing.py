"""
Structured trace for a single take_order turn.

Returned as trace dict inside the take_order response so callers
(eval runner, Streamlit UI, CLI) can display or log it without
coupling to internal agent state.

Format:
{
    "total_ms": float,
    "iterations": int,          # agent loop iterations (1 = no tools)
    "llm_calls": [
        {"iteration": int, "latency_ms": float, "stop_reason": str}
    ],
    "tool_calls": [
        {
            "iteration": int,
            "name": str,
            "input": dict,
            "latency_ms": float,
            "output_chars": int,   # proxy for response size
            "error": bool
        }
    ]
}
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Trace:
    llm_calls: list[dict] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    total_ms: float = 0.0

    def record_llm(
        self,
        iteration: int,
        latency_ms: float,
        stop_reason: str,
        usage: dict | None = None,
    ) -> None:
        """Record one LLM call.

        usage (optional) carries Anthropic's token-usage breakdown for this
        call: input_tokens (fresh non-cached input), output_tokens (generated),
        cache_creation_input_tokens (newly cached this call, billed at 1.25x),
        cache_read_input_tokens (read from prior cache, billed at 0.1x). When
        present, lets a caller verify caching is actually hitting in production
        and compute real cost from the trace rather than estimating.
        """
        entry: dict = {
            "iteration": iteration,
            "latency_ms": round(latency_ms, 1),
            "stop_reason": stop_reason,
        }
        if usage:
            entry["usage"] = {
                "input_tokens":                usage.get("input_tokens", 0) or 0,
                "output_tokens":               usage.get("output_tokens", 0) or 0,
                "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0) or 0,
                "cache_read_input_tokens":     usage.get("cache_read_input_tokens", 0) or 0,
            }
        self.llm_calls.append(entry)

    def record_tool(
        self,
        iteration: int,
        name: str,
        tool_input: dict,
        result: Any,
        latency_ms: float,
        error: bool = False,
    ) -> None:
        self.tool_calls.append({
            "iteration": iteration,
            "name": name,
            "input": tool_input,
            "latency_ms": round(latency_ms, 1),
            "output_chars": len(str(result)),
            "error": error,
        })

    def to_dict(self) -> dict:
        return {
            "total_ms": round(self.total_ms, 1),
            "iterations": len(self.llm_calls),
            "llm_calls": self.llm_calls,
            "tool_calls": self.tool_calls,
        }

    def summary_line(self) -> str:
        llm_total = sum(c["latency_ms"] for c in self.llm_calls)
        tool_total = sum(c["latency_ms"] for c in self.tool_calls)
        return (
            f"{self.total_ms:.0f}ms total | "
            f"{len(self.llm_calls)} LLM call(s) ({llm_total:.0f}ms) | "
            f"{len(self.tool_calls)} tool call(s) ({tool_total:.0f}ms)"
        )

    def format_verbose(self) -> str:
        lines = [f"  TRACE  {self.summary_line()}"]
        for llm in self.llm_calls:
            lines.append(
                f"    [iter {llm['iteration']}] LLM → {llm['stop_reason']} "
                f"({llm['latency_ms']:.0f}ms)"
            )
            tool_calls_this_iter = [
                t for t in self.tool_calls if t["iteration"] == llm["iteration"]
            ]
            for t in tool_calls_this_iter:
                import json
                input_str = json.dumps(t["input"])[:70]
                err = " ERROR" if t["error"] else ""
                lines.append(
                    f"      → {t['name']}({input_str}) "
                    f"[{t['latency_ms']:.0f}ms, {t['output_chars']} chars]{err}"
                )
        return "\n".join(lines)
