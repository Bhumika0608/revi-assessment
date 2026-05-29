#!/usr/bin/env python3
"""
Eval runner for the Talkin' Tacos ordering agent.

Usage:
  python -m evals.run_evals                    # run all cases, print report
  python -m evals.run_evals --json             # machine-readable JSON output
  python -m evals.run_evals --category simple  # run one category
  python -m evals.run_evals --model claude-haiku-4-5-20251001  # override model
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# Load .env so ANTHROPIC_API_KEY is available without needing it pre-exported
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from agent.agent import take_order
from db.logging_config import setup_logging
from db.setup import get_all_item_ids, init_db
from evals.metrics import CaseResult, TurnResult, aggregate, score_case

setup_logging()

CASES_PATH = ROOT / "evals" / "test_cases.json"


def _order_from_cart(cart: list[dict]) -> dict | None:
    """Build a scoring-shaped order from the live cart.

    The eval framework was originally written against an old place_order return shape.
    Under the current ReAct architecture, the cart at signal_checkout time IS the
    final order. We mirror its shape here (items + subtotal incl. modifier upcharges)
    so the existing item/modifier/subtotal metrics can score against it directly.
    """
    if not cart:
        return None
    return {
        "items": [
            {
                "item_id":   i["item_id"],
                "name":      i.get("name", ""),
                "quantity":  i["quantity"],
                "modifiers": list(i.get("modifiers", [])),
                "line_total": i.get("line_total", 0.0),
            }
            for i in cart
        ],
        "subtotal": round(sum(i.get("line_total", 0.0) for i in cart), 2),
    }


def run_case(case: dict, valid_ids: set[str]) -> CaseResult:
    history: list[dict] = []
    cart: list[dict] = []
    validated_ids: set[str] = set()
    turn_results: list[TurnResult] = []

    for idx, user_msg in enumerate(case["turns"]):
        t0 = time.perf_counter()
        try:
            response = take_order(
                user_msg,
                history if history else None,
                cart,
                validated_ids,
            )
        except Exception as exc:
            turn_results.append(TurnResult(
                turn_idx=idx,
                user_message=user_msg,
                agent_message=f"ERROR: {exc}",
                status="error",
                order=None,
                latency_ms=(time.perf_counter() - t0) * 1000,
            ))
            break

        latency_ms = (time.perf_counter() - t0) * 1000

        # Carry cart and validated_ids forward so multi-turn modifications work.
        cart = response.get("cart", [])
        validated_ids = response.get("validated_ids", set()) or set()

        turn_results.append(TurnResult(
            turn_idx=idx,
            user_message=user_msg,
            agent_message=response["agent_message"],
            status=response["status"],
            order=_order_from_cart(cart),
            latency_ms=latency_ms,
            trace=response.get("trace"),
        ))

        history.append({"role": "user", "content": user_msg})
        history.append({"role": "assistant", "content": response["agent_message"]})

        # Stop early only on refused — checkout-bound orders may be edited in later turns.
        if response["status"] == "refused" and idx < len(case["turns"]) - 1:
            break

    return score_case(case, turn_results, valid_ids)


def _aggregate_token_usage(results: list[CaseResult]) -> dict:
    """Sum Anthropic token usage across every LLM call in every turn of every
    case. Returns a structured block that lets a reader verify prompt caching
    is actually hitting (cache_read_input_tokens > 0) and compute real cost
    rather than estimating from latency.
    """
    total_input          = 0
    total_output         = 0
    total_cache_create   = 0
    total_cache_read     = 0
    llm_calls            = 0
    calls_with_usage     = 0

    for r in results:
        for turn in r.turns:
            trace = turn.trace or {}
            for call in trace.get("llm_calls", []):
                llm_calls += 1
                usage = call.get("usage")
                if not usage:
                    continue
                calls_with_usage   += 1
                total_input        += usage.get("input_tokens", 0)
                total_output       += usage.get("output_tokens", 0)
                total_cache_create += usage.get("cache_creation_input_tokens", 0)
                total_cache_read   += usage.get("cache_read_input_tokens", 0)

    total_input_tokens_all = total_input + total_cache_create + total_cache_read
    cache_hit_rate = (
        total_cache_read / total_input_tokens_all if total_input_tokens_all else 0.0
    )

    # Rough cost estimate using public Haiku 4.5 pricing as a default; an
    # operator running Sonnet should re-cost from the breakdown.
    HAIKU_INPUT_PER_MTOK         = 0.80
    HAIKU_OUTPUT_PER_MTOK        = 4.00
    HAIKU_CACHE_CREATE_PER_MTOK  = HAIKU_INPUT_PER_MTOK * 1.25
    HAIKU_CACHE_READ_PER_MTOK    = HAIKU_INPUT_PER_MTOK * 0.10
    est_cost_haiku_usd = round((
        total_input        * HAIKU_INPUT_PER_MTOK
        + total_cache_create * HAIKU_CACHE_CREATE_PER_MTOK
        + total_cache_read   * HAIKU_CACHE_READ_PER_MTOK
        + total_output     * HAIKU_OUTPUT_PER_MTOK
    ) / 1_000_000, 4)

    return {
        "llm_calls":                  llm_calls,
        "llm_calls_with_usage":       calls_with_usage,
        "input_tokens":               total_input,
        "output_tokens":              total_output,
        "cache_creation_input_tokens": total_cache_create,
        "cache_read_input_tokens":     total_cache_read,
        "total_input_tokens_all":     total_input_tokens_all,
        "cache_hit_rate":             round(cache_hit_rate, 4),
        "estimated_cost_usd_haiku":   est_cost_haiku_usd,
    }


def _print_trace(trace: dict) -> None:
    llm_calls = trace.get("llm_calls", [])
    tool_calls = trace.get("tool_calls", [])
    print(
        f"      TRACE: {trace['total_ms']:.0f}ms total | "
        f"{len(llm_calls)} LLM | {len(tool_calls)} tool(s)"
    )
    for llm in llm_calls:
        print(
            f"        [iter {llm['iteration']}] LLM → {llm['stop_reason']} "
            f"({llm['latency_ms']:.0f}ms)"
        )
        import json as _json
        for t in [tc for tc in tool_calls if tc["iteration"] == llm["iteration"]]:
            input_str = _json.dumps(t["input"])[:70]
            err = " ERROR" if t["error"] else ""
            print(
                f"          → {t['name']}({input_str}) "
                f"[{t['latency_ms']:.0f}ms, {t['output_chars']} chars]{err}"
            )


def _print_token_usage(usage: dict) -> None:
    if not usage or usage.get("llm_calls", 0) == 0:
        return
    print("\n  Token usage (Anthropic):")
    print(f"    LLM calls           : {usage['llm_calls']:,}")
    print(f"    Fresh input tokens  : {usage['input_tokens']:,}")
    print(f"    Cache-create tokens : {usage['cache_creation_input_tokens']:,}  (one-time, 1.25× rate)")
    print(f"    Cache-read tokens   : {usage['cache_read_input_tokens']:,}  (0.10× rate)")
    print(f"    Output tokens       : {usage['output_tokens']:,}")
    print(f"    Cache hit rate      : {usage['cache_hit_rate']*100:.1f}%")
    print(f"    Est. Haiku cost     : ${usage['estimated_cost_usd_haiku']:.4f}")


def print_report(results: list[CaseResult], agg, verbose: bool = False) -> None:
    print("\n" + "=" * 70)
    print("TALKIN' TACOS — AGENT EVAL REPORT")
    print("=" * 70)

    for r in results:
        icon = "✓" if r.passed else "✗"
        print(f"\n{icon} [{r.case_id}] {r.title}")
        if not r.passed:
            for reason in r.failure_reasons:
                print(f"    ↳ {reason}")
        for t in r.turns:
            print(f"    [{t.latency_ms:6.0f}ms] [{t.status:11s}] U: {t.user_message[:60]}")
            print(f"                             A: {t.agent_message[:60]}")
            if verbose and (not r.passed) and t.trace:
                _print_trace(t.trace)

    print("\n" + "=" * 70)
    print("AGGREGATE METRICS")
    print("=" * 70)
    print(f"  Pass rate           : {agg.passed}/{agg.total_cases}  ({agg.passed/agg.total_cases*100:.1f}%)")
    print(f"  Item ID Accuracy    : {agg.mean_iia*100:.1f}%")
    print(f"  Modifier Accuracy   : {agg.mean_modifier_acc*100:.1f}%")
    print(f"  Status Accuracy     : {agg.status_accuracy*100:.1f}%")
    print(f"  Hallucination Rate  : {agg.hallucination_rate*100:.1f}%   (target: 0%)")
    print(f"  Subtotal Accuracy   : {agg.subtotal_accuracy*100:.1f}%")
    print(f"  Clarif. Recall      : {agg.clarification_recall*100:.1f}%")
    print(f"  Refusal Precision   : {agg.refusal_precision*100:.1f}%")
    print(f"  Latency p50/p95/p99 : {agg.latency_p50:.0f}ms / {agg.latency_p95:.0f}ms / {agg.latency_p99:.0f}ms")
    print(f"  Turn Efficiency     : {agg.turn_efficiency:.2f}x  (1.0 = optimal)")

    print("\n  By Category:")
    for cat, stats in sorted(agg.by_category.items()):
        bar = "█" * int(stats["pass_rate"] * 10)
        print(f"    {cat:20s}: {stats['passed']}/{stats['total']}  {bar}")

    _print_token_usage(_aggregate_token_usage(results))

    print("=" * 70 + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="Print JSON results to stdout")
    parser.add_argument("--output", "-o", help="Write JSON results to this file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Show trace on failures")
    parser.add_argument("--category", help="Run only cases in this category")
    parser.add_argument("--id", help="Run only this specific case ID")
    parser.add_argument("--model", help="Override CLAUDE_MODEL env var")
    args = parser.parse_args()

    if args.model:
        os.environ["CLAUDE_MODEL"] = args.model

    init_db()
    valid_ids = get_all_item_ids()

    with open(CASES_PATH) as f:
        cases = json.load(f)

    if args.category:
        cases = [c for c in cases if c["category"] == args.category]
    if args.id:
        cases = [c for c in cases if c["id"] == args.id]

    if not cases:
        print("No matching test cases found.", file=sys.stderr)
        sys.exit(1)

    results: list[CaseResult] = []
    for i, case in enumerate(cases, 1):
        # Progress always goes to stderr so stdout stays clean for --json / --output
        print(f"Running {i}/{len(cases)}: {case['id']}...", end=" ", flush=True, file=sys.stderr)
        result = run_case(case, valid_ids)
        results.append(result)
        print("PASS" if result.passed else "FAIL", file=sys.stderr)

    agg = aggregate(results)
    token_usage = _aggregate_token_usage(results)

    if args.json or args.output:
        import datetime
        model_id = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
        output = {
            "model": model_id,
            "run_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "total_cases": agg.total_cases,
            "summary": {
                "total": agg.total_cases,
                "passed": agg.passed,
                "failed": agg.failed,
                "pass_rate": agg.passed / agg.total_cases,
            },
            "metrics": {
                "item_identification_accuracy": agg.mean_iia,
                "modifier_accuracy": agg.mean_modifier_acc,
                "status_accuracy": agg.status_accuracy,
                "hallucination_rate": agg.hallucination_rate,
                "subtotal_accuracy": agg.subtotal_accuracy,
                "clarification_recall": agg.clarification_recall,
                "refusal_precision": agg.refusal_precision,
                "latency_p50_ms": agg.latency_p50,
                "latency_p95_ms": agg.latency_p95,
                "latency_p99_ms": agg.latency_p99,
                "turn_efficiency": agg.turn_efficiency,
            },
            "by_category": agg.by_category,
            "token_usage": token_usage,
            "cases": [
                {
                    "id": r.case_id,
                    "title": r.title,
                    "category": r.category,
                    "passed": r.passed,
                    "failure_reasons": r.failure_reasons,
                    "iia": r.iia,
                    "modifier_acc": r.modifier_acc,
                    "subtotal_correct": r.subtotal_correct,
                    "status_correct": r.status_correct,
                    "hallucinated_ids": r.hallucinated_ids,
                    "total_turns": r.total_turns,
                }
                for r in results
            ],
        }
        json_str = json.dumps(output, indent=2)
        if args.output:
            Path(args.output).write_text(json_str)
            print(f"Results written to {args.output}", file=sys.stderr)
        if args.json:
            print(json_str)
    else:
        print_report(results, agg, verbose=args.verbose)

    sys.exit(0 if agg.failed == 0 else 1)


if __name__ == "__main__":
    main()
