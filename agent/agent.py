"""
Core ordering agent.

take_order() is the required public interface.

Agent loop design:
  - Raw Anthropic SDK (no framework) for full visibility into tool call ordering
  - Prompt caching on system prompt: ~80% token savings on repeated calls
  - Agent manages the CART via tools (add_to_cart, remove_from_cart, signal_checkout)
  - Cart state is passed in and returned — stateless function, state owned by caller
  - Status derived from tool call trace — cannot be hallucinated:
      "in_progress" → building cart
      "checkout"    → signal_checkout was called; UI takes over
      "refused"     → REFUSED: prefix in agent text
  - REFUSED: prefix in agent text signals refusal (deterministic parse)
  - Circuit breaker at MAX_ITERATIONS prevents runaway billing
  - Payment and order persistence happen OUTSIDE the agent (after payment succeeds)
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

import anthropic

from .prompts import SYSTEM_PROMPT
from .tool_schemas import TOOL_SCHEMAS
from .tools import (
    _CartCtx,
    add_to_cart,
    get_cart_contents,
    get_item_details,
    remove_from_cart,
    search_faq,
    search_menu,
    set_item_quantity,
    signal_checkout,
    update_item_modifiers,
)
from .tracing import Trace

logger = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None
MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
MAX_ITERATIONS = 8

# Per-request timeout (seconds). The SDK default is 600s — too long for a chat UI;
# 30s lines up with normal LLM response times and prevents the UI freezing on a
# hung endpoint. max_retries is the SDK's internal retry budget for transient
# errors (rate limit, 5xx, connection); the SDK applies exponential backoff.
LLM_REQUEST_TIMEOUT_S = float(os.getenv("LLM_REQUEST_TIMEOUT_S", "30.0"))
LLM_MAX_RETRIES       = int(os.getenv("LLM_MAX_RETRIES", "2"))

# Fallback messages surfaced to the customer when the LLM call ultimately fails
# after the SDK's internal retries. Kept short and non-alarming.
_FALLBACK_TIMEOUT      = "Sorry, I'm running slow right now — please try again in a moment."
_FALLBACK_RATE_LIMIT   = "I'm a bit overloaded right now — please try again in a moment."
_FALLBACK_CONNECTION   = "I'm having trouble connecting right now — please try again."
_FALLBACK_GENERIC      = "I hit a problem on my end — please try again."

# ── Done-signal short-circuit ────────────────────────────────────────────────
# When the user's message is essentially just a checkout signal AND the cart
# already has items, bypass the LLM entirely and fire signal_checkout directly.
# This guards against a known instruction-following weakness on smaller models
# where "that's all" gets reinterpreted as "I want all of that" and triggers a
# wasteful re-search → re-add → realize-duplicate → remove → re-add → checkout
# loop (4-8 extra LLM calls per checkout turn). The fast-path keeps the LLM in
# control for mixed messages ("that's all, but actually change the coke") by
# requiring the message to be both short AND free of continuation cues.

_DONE_SIGNAL_PHRASES = (
    "that's all",   "thats all",
    "that's it",    "thats it",
    "place it",     "place my order",   "place order",
    "confirm it",   "confirm my order", "confirm order",
    "go ahead",     "yes go ahead",
    "i'm done",     "im done",
    "nothing else",
    "checkout",     "check out",
    "all good",     "we're good",       "were good",
    "that's everything", "thats everything",
)
# Single-word messages — match only when the whole cleaned message equals one
# of these (avoids "Can you confirm the price?" short-circuiting).
_DONE_SIGNAL_WHOLE = {"confirm", "done"}
# Words that signal "I'm about to add or change something" — never short-circuit
# when any of these appear, even alongside a done phrase. "actually" deliberately
# NOT here: customers say "actually you know what, place it" / "actually that's
# all" — the filler doesn't override an explicit done phrase elsewhere in the
# message. Same for "also" — too weak a reversal cue to be a hard blocker.
_DONE_SIGNAL_CONTINUATIONS = {
    "but", "wait", "except", "however", "though", "instead",
}
_DONE_SIGNAL_MAX_WORDS = 5
_DONE_SIGNAL_RESPONSE  = "Perfect, heading to checkout! 🛒"

# Short negative messages — only count as done signal IN CONTEXT (i.e., when
# the previous assistant message asked an 'anything else?' style question).
# Alone they're ambiguous (could deny a clarification, not close the order).
_DONE_NEGATIVE_RESPONSES = {
    "no", "nope", "nah",
    "no thanks", "no thank you", "no thanks i'm good", "no im good",
}
# Phrases the agent uses to ask "are you done ordering?". When any of these
# appears in the previous assistant turn, a subsequent short "no" / "nope"
# from the customer is unambiguously a checkout signal.
_COMPLETION_PROMPTS = (
    "anything else",  "anything more",  "anything to add",
    "good to go",     "ready to check", "ready to checkout",
    "ready to place", "all set",        "is that all",
    "is that everything", "would you like anything else",
    "would you like to add",
)


def _is_pure_done_signal(user_message: str) -> bool:
    """True iff the user message is unambiguously a checkout signal.

    Conservative — leans toward letting the LLM handle anything that might
    carry additional intent. The cost of a false positive (premature checkout)
    is worse than the cost of a false negative (one extra LLM-driven turn).
    """
    if not user_message:
        return False
    cleaned = re.sub(r"[^\w\s']", " ", user_message.lower()).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return False
    words = cleaned.split()
    if not words or len(words) > _DONE_SIGNAL_MAX_WORDS:
        return False
    if any(w in _DONE_SIGNAL_CONTINUATIONS for w in words):
        return False
    if any(phrase in cleaned for phrase in _DONE_SIGNAL_PHRASES):
        return True
    return cleaned in _DONE_SIGNAL_WHOLE


def _is_negative_done_response(user_message: str) -> bool:
    """True iff the message is a short, clean negative ('no', 'nope', 'no thanks').
    Caller must verify the surrounding context separately — see
    `_was_asked_about_completion`. Alone these words are not done signals."""
    if not user_message:
        return False
    cleaned = re.sub(r"[^\w\s']", " ", user_message.lower()).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        return False
    words = cleaned.split()
    if not words or len(words) > 4:
        return False
    if any(w in _DONE_SIGNAL_CONTINUATIONS for w in words):
        return False
    return cleaned in _DONE_NEGATIVE_RESPONSES


def _was_asked_about_completion(conversation_history: list[dict] | None) -> bool:
    """True iff the most recent assistant turn asked an 'are you done?' style
    question. Combined with a negative response from the customer, this is an
    unambiguous done signal."""
    if not conversation_history:
        return False
    for msg in reversed(conversation_history):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content", "")
        if isinstance(content, list):
            text_parts = []
            for b in content:
                # tool_use content blocks — pull out any plain-text blocks
                if isinstance(b, dict) and b.get("type") == "text":
                    text_parts.append(b.get("text", ""))
                elif hasattr(b, "text"):
                    text_parts.append(getattr(b, "text", ""))
            content = " ".join(text_parts)
        if not isinstance(content, str):
            return False
        lower = content.lower()
        return any(prompt in lower for prompt in _COMPLETION_PROMPTS)
    return False


def _should_short_circuit_to_checkout(
    user_message: str,
    conversation_history: list[dict] | None,
) -> bool:
    """Combined check: an unambiguous done phrase, OR a short negative response
    after the agent asked 'anything else?'. Both indicate the customer is
    finished and we should fire signal_checkout without invoking the LLM."""
    if _is_pure_done_signal(user_message):
        return True
    if (_is_negative_done_response(user_message)
            and _was_asked_about_completion(conversation_history)):
        return True
    return False


def _contains_done_signal_anywhere(user_message: str) -> bool:
    """True if the user message contains a done-signal phrase anywhere in
    the message, with no continuation cues.

    More permissive than _is_pure_done_signal (no max-word cap) — used as a
    POST-hoc check after the agent loop has already populated the cart. The
    pattern this covers: customer types a complete order AND a done signal in
    a single message ("I'll have 20 birria tacos, that's all"). The cart was
    empty when the turn arrived, so the pre-LLM fast-path bypassed; the LLM
    added the items correctly but didn't fire signal_checkout on its own."""
    if not user_message:
        return False
    cleaned = re.sub(r"[^\w\s']", " ", user_message.lower())
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned:
        return False
    if any(w in _DONE_SIGNAL_CONTINUATIONS for w in cleaned.split()):
        return False
    return any(phrase in cleaned for phrase in _DONE_SIGNAL_PHRASES)


# ── FAQ short-circuit ────────────────────────────────────────────────────────
# When the customer asks a clear restaurant-info question (hours, address,
# parking, delivery, payment methods, dietary options), search_faq alone can
# answer — but the LLM-driven path costs 2 calls (decide-tool, then format).
# Detect these unambiguously and bypass the LLM, calling search_faq directly.
# Conservative on false positives: missed FAQs still fall through to the LLM
# (which handles them correctly); falsely short-circuiting an order question
# would lose the LLM's ability to ask a clarifying question, so we err safe.

_FAQ_TRIGGER_PHRASES = (
    # Hours / open status
    "what time", "what hours", "are you open", "when do you open",
    "when do you close", "open today", "open now", "still open",
    # Location / contact
    "where are you", "where is the restaurant", "what's your address",
    "your address", "your location", "phone number", "your phone",
    # Amenities
    "do you have parking", "do you have wifi", "is there parking",
    # Service options
    "do you deliver", "do you do delivery", "do you offer delivery",
    "do you cater", "do you do catering",
    "do you take reservations", "take reservations",
    # Payment
    "do you take credit", "do you accept",
    # Dietary (search_faq handles these via _dietary_answer over live menu)
    "do you have vegan", "do you have gluten free", "do you have gluten-free",
    "do you have vegetarian", "anything vegan", "anything gluten",
    "what's vegan", "what's gluten",
)

# If any of these substrings appear, never short-circuit — the customer is
# ordering, not asking an info question.
_FAQ_ORDERING_BLOCKERS = (
    "i want", "i'd like", "id like", "i'll have", "ill have",
    "can i get", "give me", "i'll take", "ill take",
    "let me have", "let me get", "i'll order",
)

# If any of these menu words appear, route through the LLM — the customer is
# asking about specific menu items, not restaurant facts.
_FAQ_MENU_ITEM_BLOCKERS = {
    "taco", "tacos", "burrito", "burritos", "bowl", "bowls",
    "quesadilla", "quesadillas", "nachos", "torta", "tortas",
    "chips", "flan", "churros", "horchata", "jarritos", "coke",
    "sopapilla", "sopapillas", "tamale", "tamales", "elote", "salsa",
}

_FAQ_MAX_WORDS = 12


def _is_faq_query(user_message: str) -> bool:
    """True iff the user message is unambiguously a restaurant-info question
    that search_faq alone can answer. Conservative — when in doubt, false."""
    if not user_message:
        return False
    cleaned = user_message.lower().strip()
    if not cleaned or len(cleaned.split()) > _FAQ_MAX_WORDS:
        return False
    if any(blocker in cleaned for blocker in _FAQ_ORDERING_BLOCKERS):
        return False
    # Tokenize for menu-item word check (substring match would false-positive
    # on "talk" containing "alk" style overlaps).
    words = set(re.sub(r"[^\w\s]", " ", cleaned).split())
    if words & _FAQ_MENU_ITEM_BLOCKERS:
        return False
    return any(phrase in cleaned for phrase in _FAQ_TRIGGER_PHRASES)


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        if not os.getenv("ANTHROPIC_API_KEY"):
            try:
                from dotenv import load_dotenv
                load_dotenv()
            except ImportError:
                pass
        _client = anthropic.Anthropic(
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            timeout=LLM_REQUEST_TIMEOUT_S,
            max_retries=LLM_MAX_RETRIES,
        )
    return _client


_SYSTEM = [
    {
        "type": "text",
        "text": SYSTEM_PROMPT,
        "cache_control": {"type": "ephemeral"},
    }
]

# Tool definitions are static and re-sent on every LLM call (~700 tokens). Mark
# the last tool with cache_control so the entire tool block rides alongside the
# system-prompt cache. Anthropic caches up to and including the marked element.
_TOOLS: list[dict] = [dict(t) for t in TOOL_SCHEMAS]
if _TOOLS:
    _TOOLS[-1] = {**_TOOLS[-1], "cache_control": {"type": "ephemeral"}}


def _with_history_cache_breakpoint(messages: list[dict]) -> list[dict]:
    """Tag the last assistant message with cache_control so the conversation
    history prefix is cached on subsequent requests within the 5-minute TTL.

    Anthropic caches everything up to and including the marker. We pick the
    last assistant message — not the last user message — because the current
    turn's user input changes every call, so caching past it adds nothing. The
    last assistant boundary is the stable suffix of the cacheable prefix.

    Returns a new list — the input is not mutated. ContentBlock objects from
    prior LLM responses are converted to dicts so cache_control can be attached.
    """
    if not messages:
        return messages
    last_asst_idx = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "assistant":
            last_asst_idx = i
            break
    if last_asst_idx is None:
        return messages   # First turn — no prior assistant boundary to cache to.

    out = list(messages)
    msg = dict(out[last_asst_idx])
    content = msg["content"]

    if isinstance(content, str):
        msg["content"] = [{
            "type": "text",
            "text": content,
            "cache_control": {"type": "ephemeral"},
        }]
    elif isinstance(content, list):
        # Convert any SDK ContentBlock objects (TextBlock, ToolUseBlock) to
        # plain dicts so we can attach cache_control to the last block.
        new_blocks: list[dict] = []
        for block in content:
            if isinstance(block, dict):
                new_blocks.append(dict(block))
            elif hasattr(block, "model_dump"):
                new_blocks.append(block.model_dump(exclude_none=True))
            elif hasattr(block, "dict"):
                new_blocks.append(block.dict(exclude_none=True))
            else:
                new_blocks.append({
                    k: getattr(block, k)
                    for k in ("type", "text", "id", "name", "input")
                    if hasattr(block, k)
                })
        if new_blocks:
            new_blocks[-1] = {**new_blocks[-1], "cache_control": {"type": "ephemeral"}}
        msg["content"] = new_blocks
    else:
        return messages   # Unknown content shape — leave it alone.

    out[last_asst_idx] = msg
    return out


def take_order(
    user_message: str,
    conversation_history: list[dict] | None = None,
    current_cart: list[dict] | None = None,
    validated_ids: set[str] | None = None,
) -> dict:
    """
    Returns:
    {
        "agent_message": str,
        "status":        str,        # "in_progress" | "checkout" | "refused"
        "cart":          list[dict], # updated cart after this turn
        "validated_ids": set[str],   # item_ids verified this session (persist across turns)
        "trace":         dict,
    }
    """
    messages  = _build_messages(user_message, conversation_history or [])
    cart_ctx  = _CartCtx(current_cart or [])
    seed_ids  = validated_ids or set()

    t0 = time.perf_counter()

    # Fast-path: skip the LLM when the user message is unambiguously a checkout
    # signal — either an explicit done phrase ("that's all", "place it") OR a
    # short negative ("no", "nope") in response to an "anything else?" question.
    # Deterministic, ~0 LLM calls, removes the bad behavior where some models
    # re-verify the cart by re-running search → add → realize-duplicate → remove
    # → re-add before checking out.
    if cart_ctx.cart and _should_short_circuit_to_checkout(user_message, conversation_history):
        trace = Trace()
        t_tool = time.perf_counter()
        result = signal_checkout(cart_ctx)
        tool_ms = (time.perf_counter() - t_tool) * 1000
        is_error = isinstance(result, dict) and "error" in result
        trace.record_tool(1, "signal_checkout", {}, result, tool_ms, error=is_error)
        if is_error:
            # signal_checkout failed (shouldn't happen — cart is non-empty by
            # the if-guard above — but guard anyway). Fall through to the LLM
            # so it can apologise and ask the customer what to do.
            cart_ctx.checkout_signaled = False
        else:
            cart_ctx.checkout_signaled = True
        if cart_ctx.checkout_signaled:
            trace.total_ms = (time.perf_counter() - t0) * 1000
            return {
                "agent_message": _DONE_SIGNAL_RESPONSE,
                "status":        _derive_status(_DONE_SIGNAL_RESPONSE, True),
                "cart":          cart_ctx.cart,
                "validated_ids": seed_ids,
                "trace":         trace.to_dict(),
            }

    # Fast-path: skip the LLM when the message is an unambiguous restaurant-info
    # query (hours, address, parking, dietary options). search_faq alone can
    # answer; the LLM-driven path would burn 2 calls (decide-tool, then format).
    # Falls through to the LLM if search_faq returns low confidence — preserves
    # the LLM's ability to interpret edge cases.
    if _is_faq_query(user_message):
        trace = Trace()
        t_tool = time.perf_counter()
        faq_result = search_faq(user_message)
        tool_ms = (time.perf_counter() - t_tool) * 1000
        trace.record_tool(
            1, "search_faq", {"query": user_message},
            faq_result, tool_ms,
            error=not faq_result.get("found", False),
        )
        if faq_result.get("found") and faq_result.get("answer"):
            trace.total_ms = (time.perf_counter() - t0) * 1000
            return {
                "agent_message": faq_result["answer"],
                "status":        "in_progress",
                "cart":          cart_ctx.cart,
                "validated_ids": seed_ids,
                "trace":         trace.to_dict(),
            }
        # else: search_faq couldn't confidently match — let the LLM decide
        # (it might interpret the message as something other than an FAQ).

    agent_text, trace, session_validated_ids = _run_loop(
        messages, cart_ctx, seed_validated_ids=seed_ids
    )

    # Post-LLM done-signal check. If the agent built the cart but didn't fire
    # signal_checkout, AND the user's original message contained an unambiguous
    # done phrase, fire it ourselves. Handles "I'll have 20 birria tacos,
    # that's all" — one message with both the order AND the done signal. The
    # pre-LLM fast-path skips this case (cart is empty until the LLM adds);
    # the LLM sometimes leaves checkout pending on its own.
    if (not cart_ctx.checkout_signaled
            and cart_ctx.cart
            and _contains_done_signal_anywhere(user_message)):
        t_tool = time.perf_counter()
        result = signal_checkout(cart_ctx)
        tool_ms = (time.perf_counter() - t_tool) * 1000
        is_error = isinstance(result, dict) and "error" in result
        trace.record_tool(
            len(trace.llm_calls) + 1,
            "signal_checkout", {},
            result, tool_ms, error=is_error,
        )
        if not is_error:
            cart_ctx.checkout_signaled = True

    trace.total_ms = (time.perf_counter() - t0) * 1000

    status = _derive_status(agent_text, cart_ctx.checkout_signaled)

    display_text = agent_text
    if display_text.startswith("REFUSED:"):
        display_text = display_text[len("REFUSED:"):].strip()

    return {
        "agent_message": display_text,
        "status":        status,
        "cart":          cart_ctx.cart,
        "validated_ids": session_validated_ids,
        "trace":         trace.to_dict(),
    }


# ── Internal helpers ────────────────────────────────────────────────────────


def _build_messages(user_message: str, history: list[dict]) -> list[dict]:
    messages = list(history)
    messages.append({"role": "user", "content": user_message})
    return messages


def _run_loop(
    messages: list[dict],
    cart_ctx: _CartCtx,
    seed_validated_ids: set[str] | None = None,
) -> tuple[str, Trace, set[str]]:
    # Seed from previous turns so agent doesn't re-validate items already seen
    validated_ids: set[str] = set(seed_validated_ids or set())
    trace  = Trace()
    client = _get_client()

    for iteration in range(1, MAX_ITERATIONS + 1):
        t_llm = time.perf_counter()
        try:
            response = client.messages.create(
                model=MODEL,
                system=_SYSTEM,
                messages=_with_history_cache_breakpoint(messages),
                tools=_TOOLS,
                max_tokens=1024,
                temperature=0,
            )
        except anthropic.APITimeoutError:
            logger.exception("LLM timeout on iter %d (after %d retries)",
                             iteration, LLM_MAX_RETRIES)
            trace.record_llm(iteration, (time.perf_counter() - t_llm) * 1000, "timeout")
            return _FALLBACK_TIMEOUT, trace, validated_ids
        except anthropic.RateLimitError:
            logger.exception("LLM rate-limited on iter %d (after %d retries)",
                             iteration, LLM_MAX_RETRIES)
            trace.record_llm(iteration, (time.perf_counter() - t_llm) * 1000, "rate_limit")
            return _FALLBACK_RATE_LIMIT, trace, validated_ids
        except anthropic.APIConnectionError:
            logger.exception("LLM connection error on iter %d (after %d retries)",
                             iteration, LLM_MAX_RETRIES)
            trace.record_llm(iteration, (time.perf_counter() - t_llm) * 1000, "connection_error")
            return _FALLBACK_CONNECTION, trace, validated_ids
        except anthropic.APIError:
            logger.exception("LLM API error on iter %d", iteration)
            trace.record_llm(iteration, (time.perf_counter() - t_llm) * 1000, "api_error")
            return _FALLBACK_GENERIC, trace, validated_ids

        # Capture token usage for cost observability. Anthropic returns four
        # buckets: fresh input, generated output, cache create (1.25x rate,
        # one-time), cache read (0.1x rate). Logging this per call lets us
        # verify caching is actually hitting and compute real cost from the
        # trace instead of estimating.
        usage_dict = _extract_usage(getattr(response, "usage", None))
        if usage_dict:
            logger.info(
                "LLM iter=%d stop=%s in=%d cache_read=%d cache_create=%d out=%d",
                iteration, response.stop_reason,
                usage_dict.get("input_tokens", 0),
                usage_dict.get("cache_read_input_tokens", 0),
                usage_dict.get("cache_creation_input_tokens", 0),
                usage_dict.get("output_tokens", 0),
            )

        trace.record_llm(
            iteration,
            (time.perf_counter() - t_llm) * 1000,
            response.stop_reason,
            usage=usage_dict,
        )

        if response.stop_reason == "end_turn":
            return _extract_text(response), trace, validated_ids

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                t_tool = time.perf_counter()
                result, is_checkout = _dispatch_tool(block.name, block.input, validated_ids, cart_ctx)
                tool_ms = (time.perf_counter() - t_tool) * 1000
                error = isinstance(result, dict) and "error" in result
                trace.record_tool(iteration, block.name, block.input, result, tool_ms, error=error)

                if is_checkout and not error:
                    cart_ctx.checkout_signaled = True

                tool_results.append({
                    "type":        "tool_result",
                    "tool_use_id": block.id,
                    "content":     json.dumps(result),
                })

            messages.append({"role": "assistant", "content": response.content})
            messages.append({"role": "user",      "content": tool_results})

        else:
            break

    return (
        _extract_text(response) or "I'm having trouble right now. Please try again.",
        trace,
        validated_ids,
    )


def _dispatch_tool(
    name: str,
    tool_input: dict,
    validated_ids: set[str],
    cart_ctx: _CartCtx,
) -> tuple[Any, bool]:
    """
    Execute a tool. Returns (result, is_checkout_signal).

    Enforces tool chain at the Python layer:
    - get_item_details: registers item_id in validated_ids on success.
    - add_to_cart: blocked if item_id not in validated_ids.
    """
    is_checkout = False
    try:
        if name == "search_menu":
            result = search_menu(tool_input["query"])

        elif name == "search_faq":
            result = search_faq(tool_input["query"])

        elif name == "get_item_details":
            result = get_item_details(tool_input["item_id"])
            if "error" not in result:
                validated_ids.add(tool_input["item_id"])

        elif name == "add_to_cart":
            item_id = tool_input["item_id"]
            if item_id not in validated_ids:
                result = {
                    "error": (
                        f"Cannot add '{item_id}' to cart. "
                        f"Call get_item_details('{item_id}') first to validate it."
                    )
                }
            else:
                result = add_to_cart(
                    item_id=item_id,
                    quantity=int(tool_input.get("quantity", 1)),
                    modifiers=tool_input.get("modifiers", []) or [],
                    cart_ctx=cart_ctx,
                    options=tool_input.get("options") or None,
                )

        elif name == "remove_from_cart":
            result = remove_from_cart(
                tool_input["item_id"],
                cart_ctx,
                options=tool_input.get("options") or None,
            )

        elif name == "update_item_modifiers":
            result = update_item_modifiers(
                item_id=tool_input["item_id"],
                modifiers=tool_input.get("modifiers", []) or [],
                cart_ctx=cart_ctx,
                options=tool_input.get("options") or None,
            )

        elif name == "set_item_quantity":
            result = set_item_quantity(
                item_id=tool_input["item_id"],
                quantity=int(tool_input.get("quantity", 1)),
                cart_ctx=cart_ctx,
                options=tool_input.get("options") or None,
            )

        elif name == "get_cart":
            result = get_cart_contents(cart_ctx)

        elif name == "signal_checkout":
            is_checkout = True
            result = signal_checkout(cart_ctx)
            if "error" in result:
                is_checkout = False

        else:
            result = {"error": f"Unknown tool: {name}"}

    except Exception as exc:
        logger.exception("Tool %s raised", name)
        result = {"error": str(exc)}

    return result, is_checkout


def _extract_text(response) -> str:
    for block in response.content:
        if hasattr(block, "text"):
            return block.text
    return ""


def _extract_usage(usage) -> dict | None:
    """Pull Anthropic's usage breakdown out of a response into a plain dict.

    Anthropic returns a Pydantic Usage object on every successful response.
    We want four numeric fields — fresh input, output, cache create, cache
    read — so downstream code (trace, log, eval aggregator) can sum them
    without depending on the SDK's object shape.
    """
    if usage is None:
        return None
    if hasattr(usage, "model_dump"):
        raw = usage.model_dump(exclude_none=True)
    elif hasattr(usage, "dict"):
        raw = usage.dict(exclude_none=True)
    elif isinstance(usage, dict):
        raw = usage
    else:
        raw = {
            k: getattr(usage, k, 0)
            for k in ("input_tokens", "output_tokens",
                      "cache_creation_input_tokens", "cache_read_input_tokens")
            if hasattr(usage, k)
        }
    # Coerce to int and default missing fields to 0.
    return {
        "input_tokens":                int(raw.get("input_tokens", 0) or 0),
        "output_tokens":               int(raw.get("output_tokens", 0) or 0),
        "cache_creation_input_tokens": int(raw.get("cache_creation_input_tokens", 0) or 0),
        "cache_read_input_tokens":     int(raw.get("cache_read_input_tokens", 0) or 0),
    }


def _derive_status(agent_text: str, checkout_signaled: bool) -> str:
    """
    Status is derived from the tool call trace — never from the LLM's own words.
    - signal_checkout called → "checkout"
    - REFUSED: prefix      → "refused"
    - everything else      → "in_progress"
    """
    if checkout_signaled:
        return "checkout"
    if agent_text.startswith("REFUSED:"):
        return "refused"
    return "in_progress"
