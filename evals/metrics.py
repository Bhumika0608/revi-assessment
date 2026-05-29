"""
Evaluation metrics for the Talkin' Tacos ordering agent.

  IIA           — Item Identification Accuracy: did the agent pick the right item IDs?
  modifier_acc  — Modifier Accuracy: did requested modifiers appear in the order?
  hallucination — Did the agent invent menu items that don't exist?
  subtotal_acc  — Is the computed price within $0.01 of expected?
  status_acc    — Does returned status match expected_status?
  clarification_recall  — When ambiguity existed, did agent ask for clarification?
  refusal_precision     — Did agent refuse exactly what should be refused?
  turn_efficiency       — Average turns taken vs minimum possible
  latency_p50/p95/p99   — Per-turn latency distribution
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class TurnResult:
    turn_idx: int
    user_message: str
    agent_message: str
    status: str
    order: dict | None
    latency_ms: float
    trace: dict | None = None


@dataclass
class CaseResult:
    case_id: str
    title: str
    category: str
    passed: bool
    turns: list[TurnResult] = field(default_factory=list)
    failure_reasons: list[str] = field(default_factory=list)

    # Per-metric scores (None = not applicable for this case)
    status_correct: bool | None = None
    iia: float | None = None                    # 0.0–1.0
    modifier_acc: float | None = None           # 0.0–1.0
    hallucinated_ids: list[str] = field(default_factory=list)
    subtotal_correct: bool | None = None
    clarification_given: bool | None = None     # for ambiguous cases
    refusal_given: bool | None = None           # for refusal cases
    total_turns: int = 0
    min_turns: int = 1


def score_case(case: dict, turn_results: list[TurnResult], valid_ids: set[str]) -> CaseResult:
    """Compute all metrics for a single test case given its turn results.

    Status mapping — test cases were authored with expected_status='confirmed', which
    under the current architecture means the agent fired signal_checkout (status='checkout'
    in the agent's return). We treat the cart at that turn as the final order to score.
    """
    final = turn_results[-1] if turn_results else None

    # The turn where signal_checkout fired — its cart is the authoritative final order.
    last_checkout_turn = next(
        (t for t in reversed(turn_results) if t.status == "checkout" and t.order),
        None,
    )

    result = CaseResult(
        case_id=case["id"],
        title=case["title"],
        category=case["category"],
        passed=True,
        turns=turn_results,
        total_turns=len(turn_results),
        min_turns=len(case["turns"]),
    )

    if final is None:
        result.passed = False
        result.failure_reasons.append("No turns executed")
        return result

    # ── Status accuracy ───────────────────────────────────────────────────────
    expected_status = case.get("expected_status")
    any_checkout = any(t.status == "checkout" for t in turn_results)
    if expected_status:
        if expected_status == "confirmed":
            # Legacy authoring convention — passes when signal_checkout was called.
            result.status_correct = any_checkout
        elif expected_status == "refused":
            result.status_correct = final.status == "refused"
        else:
            result.status_correct = final.status == expected_status
        if not result.status_correct:
            result.passed = False
            result.failure_reasons.append(
                f"Status: expected '{expected_status}', got '{final.status}'"
            )

    # Score item/modifier/subtotal against the cart at signal_checkout time.
    scoring_order = last_checkout_turn.order if last_checkout_turn else None
    any_confirmed = any_checkout  # alias kept for the IIA branch below

    # ── Item Identification Accuracy (IIA) ────────────────────────────────────
    expected_items = case.get("expected_items")
    if expected_items and scoring_order:
        expected_ids = {e["item_id"] for e in expected_items}
        actual_ids = {i["item_id"] for i in scoring_order.get("items", [])}
        correct = expected_ids & actual_ids
        result.iia = len(correct) / len(expected_ids) if expected_ids else 1.0

        missing = expected_ids - actual_ids
        extra = actual_ids - expected_ids
        if missing:
            result.passed = False
            result.failure_reasons.append(f"Missing items: {missing}")
        if extra:
            # Agent added items the customer never requested
            result.passed = False
            result.failure_reasons.append(f"Unexpected items in order: {extra}")
    elif expected_items and not any_confirmed:
        result.iia = 0.0
        result.passed = False
        result.failure_reasons.append("Order expected but not confirmed")

    # ── Quantity accuracy ─────────────────────────────────────────────────────
    if expected_items and scoring_order:
        actual_by_id = {i["item_id"]: i for i in scoring_order.get("items", [])}
        for e in expected_items:
            if "quantity" not in e:
                continue
            item_id = e["item_id"]
            expected_qty = e["quantity"]
            actual_qty = actual_by_id.get(item_id, {}).get("quantity", None)
            if actual_qty is not None and actual_qty != expected_qty:
                result.passed = False
                result.failure_reasons.append(
                    f"Wrong quantity for {item_id}: expected {expected_qty}, got {actual_qty}"
                )

    # ── Modifier Accuracy ─────────────────────────────────────────────────────
    if expected_items and scoring_order:
        actual_by_id = {i["item_id"]: i for i in scoring_order.get("items", [])}
        expected_mod_sets: dict[str, set[str]] = {}
        for e in expected_items:
            if "modifiers" in e:
                expected_mod_sets[e["item_id"]] = set(e["modifiers"])

        if expected_mod_sets:
            correct_mods = 0
            total_expected_mods = 0
            for item_id, expected_mods in expected_mod_sets.items():
                total_expected_mods += len(expected_mods)
                actual_mods = set(actual_by_id.get(item_id, {}).get("modifiers", []))
                correct_mods += len(expected_mods & actual_mods)
                missed = expected_mods - actual_mods
                if missed:
                    result.passed = False
                    result.failure_reasons.append(f"Missing modifiers on {item_id}: {missed}")

            result.modifier_acc = correct_mods / total_expected_mods if total_expected_mods else 1.0

    # ── Hallucination check — item IDs and modifier IDs ───────────────────────
    if scoring_order:
        from db.setup import get_item_by_id

        actual_ids = {i["item_id"] for i in scoring_order.get("items", [])}
        hallucinated_items = actual_ids - valid_ids
        result.hallucinated_ids = list(hallucinated_items)
        if hallucinated_items:
            result.passed = False
            result.failure_reasons.append(f"Hallucinated item IDs: {hallucinated_items}")

        # Check that modifier IDs exist on the actual menu item
        for item in scoring_order.get("items", []):
            item_id = item["item_id"]
            if item_id not in valid_ids:
                continue  # already flagged above
            menu_item = get_item_by_id(item_id)
            if menu_item is None:
                continue
            valid_mod_ids = {m["id"] for m in menu_item.get("modifiers", [])}
            for mod_id in item.get("modifiers", []):
                if mod_id not in valid_mod_ids:
                    result.passed = False
                    result.hallucinated_ids.append(mod_id)
                    result.failure_reasons.append(
                        f"Hallucinated modifier '{mod_id}' on {item_id} (not in menu)"
                    )

    # ── Subtotal accuracy ─────────────────────────────────────────────────────
    expected_subtotal = case.get("expected_subtotal")
    if expected_subtotal is not None and scoring_order and scoring_order.get("subtotal") is not None:
        diff = abs(scoring_order["subtotal"] - expected_subtotal)
        result.subtotal_correct = diff <= 0.02          # 2-cent tolerance
        if not result.subtotal_correct:
            result.passed = False
            result.failure_reasons.append(
                f"Subtotal: expected ${expected_subtotal:.2f}, got ${scoring_order['subtotal']:.2f}"
            )

    # ── Clarification recall ──────────────────────────────────────────────────
    if case.get("requires_clarification"):
        # For single-turn ambiguous cases, agent should NOT have confirmed
        result.clarification_given = final.status == "in_progress"
        if not result.clarification_given:
            result.passed = False
            result.failure_reasons.append("Should have asked for clarification but didn't")

    # ── Refusal check ─────────────────────────────────────────────────────────
    if case.get("expected_status") == "refused":
        result.refusal_given = final.status == "refused"
        if not result.refusal_given:
            result.passed = False
            result.failure_reasons.append(f"Should have refused but returned status '{final.status}'")

    return result


@dataclass
class AggregateMetrics:
    total_cases: int = 0
    passed: int = 0
    failed: int = 0

    # Accuracy metrics (mean across applicable cases)
    mean_iia: float = 0.0
    mean_modifier_acc: float = 0.0
    status_accuracy: float = 0.0
    hallucination_rate: float = 0.0       # fraction of orders with any hallucination
    subtotal_accuracy: float = 0.0
    clarification_recall: float = 0.0
    refusal_precision: float = 0.0

    # Latency (ms) across all turns
    latency_p50: float = 0.0
    latency_p95: float = 0.0
    latency_p99: float = 0.0
    mean_latency: float = 0.0

    turn_efficiency: float = 0.0          # mean(actual/min) — lower is better

    by_category: dict[str, dict] = field(default_factory=dict)


def aggregate(results: list[CaseResult]) -> AggregateMetrics:
    import statistics

    agg = AggregateMetrics(total_cases=len(results))
    agg.passed = sum(1 for r in results if r.passed)
    agg.failed = agg.total_cases - agg.passed

    # Collect per-metric lists
    iia_vals, mod_vals, status_vals, halluc_vals = [], [], [], []
    subtotal_vals, clarif_vals, refusal_vals = [], [], []
    latencies, turn_effs = [], []

    for r in results:
        if r.iia is not None:
            iia_vals.append(r.iia)
        if r.modifier_acc is not None:
            mod_vals.append(r.modifier_acc)
        if r.status_correct is not None:
            status_vals.append(float(r.status_correct))
        halluc_vals.append(1.0 if r.hallucinated_ids else 0.0)
        if r.subtotal_correct is not None:
            subtotal_vals.append(float(r.subtotal_correct))
        if r.clarification_given is not None:
            clarif_vals.append(float(r.clarification_given))
        if r.refusal_given is not None:
            refusal_vals.append(float(r.refusal_given))

        for t in r.turns:
            latencies.append(t.latency_ms)

        if r.min_turns > 0:
            turn_effs.append(r.total_turns / r.min_turns)

    def mean(lst):
        return statistics.mean(lst) if lst else 0.0

    def percentile(lst, pct):
        if not lst:
            return 0.0
        sorted_lst = sorted(lst)
        idx = int(len(sorted_lst) * pct / 100)
        return sorted_lst[min(idx, len(sorted_lst) - 1)]

    agg.mean_iia = mean(iia_vals)
    agg.mean_modifier_acc = mean(mod_vals)
    agg.status_accuracy = mean(status_vals)
    agg.hallucination_rate = mean(halluc_vals)
    agg.subtotal_accuracy = mean(subtotal_vals)
    agg.clarification_recall = mean(clarif_vals)
    agg.refusal_precision = mean(refusal_vals)
    agg.mean_latency = mean(latencies)
    agg.latency_p50 = percentile(latencies, 50)
    agg.latency_p95 = percentile(latencies, 95)
    agg.latency_p99 = percentile(latencies, 99)
    agg.turn_efficiency = mean(turn_effs)

    # By-category breakdown
    cats: dict[str, list[CaseResult]] = {}
    for r in results:
        cats.setdefault(r.category, []).append(r)
    for cat, cat_results in cats.items():
        agg.by_category[cat] = {
            "total": len(cat_results),
            "passed": sum(1 for r in cat_results if r.passed),
            "pass_rate": sum(1 for r in cat_results if r.passed) / len(cat_results),
        }

    return agg
