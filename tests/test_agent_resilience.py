"""
Tests that the agent loop tolerates Anthropic SDK failures.

Verifies:
  - Client is configured with a non-default timeout + retry budget.
  - APITimeoutError, RateLimitError, APIConnectionError, generic APIError all
    return a graceful customer-facing fallback (not a raised exception).
  - The agent loop records the failure in its trace with the right stop_reason.

No real API calls — _client is monkey-patched to a fake that raises.

Run: python3 -m pytest tests/test_agent_resilience.py -v
"""

import logging
import sys
from pathlib import Path

import anthropic
import httpx
import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from agent import agent as agent_mod
from db.setup import init_db


@pytest.fixture(scope="session", autouse=True)
def db():
    init_db()


@pytest.fixture
def reset_client():
    """Each test gets a clean _client slot so monkey-patching doesn't leak."""
    original = agent_mod._client
    yield
    agent_mod._client = original


def _request() -> httpx.Request:
    return httpx.Request("POST", "https://api.anthropic.com/v1/messages")


def _response(status_code: int) -> httpx.Response:
    return httpx.Response(status_code=status_code, request=_request())


class _FakeClient:
    """Stand-in for anthropic.Anthropic — its .messages.create raises `exc` every call."""
    def __init__(self, exc: Exception):
        outer = self
        class _Messages:
            def create(self, **_kwargs):
                raise outer._exc
        self._exc      = exc
        self.messages  = _Messages()


# ── Client configuration ──────────────────────────────────────────────────────

class TestClientConfig:
    def test_request_timeout_is_short(self):
        # The SDK default is 600s — we must override to keep the UI responsive.
        assert agent_mod.LLM_REQUEST_TIMEOUT_S <= 60.0

    def test_max_retries_is_set(self):
        assert agent_mod.LLM_MAX_RETRIES >= 1


# ── Each terminal error → graceful fallback, no raise ─────────────────────────

class TestErrorHandling:
    def test_timeout_returns_friendly_message(self, reset_client, monkeypatch, caplog):
        agent_mod._client = _FakeClient(anthropic.APITimeoutError(request=_request()))

        with caplog.at_level(logging.ERROR, logger="agent.agent"):
            result = agent_mod.take_order("two birria tacos")

        assert result["status"] == "in_progress"
        assert "slow" in result["agent_message"].lower() or "try again" in result["agent_message"].lower()
        assert result["cart"] == []   # nothing added; agent never reached add_to_cart
        assert any(c["stop_reason"] == "timeout" for c in result["trace"]["llm_calls"])
        assert any("LLM timeout" in rec.message for rec in caplog.records)

    def test_rate_limit_returns_friendly_message(self, reset_client, caplog):
        exc = anthropic.RateLimitError(
            "rate limited", response=_response(429), body=None,
        )
        agent_mod._client = _FakeClient(exc)

        with caplog.at_level(logging.ERROR, logger="agent.agent"):
            result = agent_mod.take_order("hi")

        assert "overloaded" in result["agent_message"].lower() or "try again" in result["agent_message"].lower()
        assert any(c["stop_reason"] == "rate_limit" for c in result["trace"]["llm_calls"])
        assert any("rate-limited" in rec.message for rec in caplog.records)

    def test_connection_error_returns_friendly_message(self, reset_client, caplog):
        exc = anthropic.APIConnectionError(request=_request())
        agent_mod._client = _FakeClient(exc)

        with caplog.at_level(logging.ERROR, logger="agent.agent"):
            result = agent_mod.take_order("hi")

        assert "trouble connecting" in result["agent_message"].lower() or "try again" in result["agent_message"].lower()
        assert any(c["stop_reason"] == "connection_error" for c in result["trace"]["llm_calls"])
        assert any("connection error" in rec.message for rec in caplog.records)

    def test_generic_api_error_returns_friendly_message(self, reset_client, caplog):
        # An APIStatusError that isn't a rate limit (e.g. 500).
        exc = anthropic.APIStatusError(
            "internal error", response=_response(500), body=None,
        )
        agent_mod._client = _FakeClient(exc)

        with caplog.at_level(logging.ERROR, logger="agent.agent"):
            result = agent_mod.take_order("hi")

        assert "problem" in result["agent_message"].lower() or "try again" in result["agent_message"].lower()
        assert any(c["stop_reason"] == "api_error" for c in result["trace"]["llm_calls"])
        assert any("LLM API error" in rec.message for rec in caplog.records)


# ── Status derivation under failure ───────────────────────────────────────────

class TestStatusAfterFailure:
    def test_status_is_in_progress_not_checkout(self, reset_client):
        agent_mod._client = _FakeClient(anthropic.APITimeoutError(request=_request()))
        result = agent_mod.take_order("two birria tacos")
        assert result["status"] == "in_progress"

    def test_cart_unchanged_after_failure(self, reset_client):
        agent_mod._client = _FakeClient(anthropic.APITimeoutError(request=_request()))
        original_cart = [{"item_id": "taco_birria", "name": "Birria Taco",
                          "price": 4.99, "quantity": 1, "modifiers": [],
                          "line_total": 4.99}]
        result = agent_mod.take_order("add another", current_cart=original_cart)
        # The agent never made progress, so the cart we passed in is returned as-is.
        assert result["cart"] == original_cart

    def test_validated_ids_preserved_after_failure(self, reset_client):
        agent_mod._client = _FakeClient(anthropic.APITimeoutError(request=_request()))
        seed_ids = {"taco_birria", "drink_coke_mexican"}
        result = agent_mod.take_order("add another", validated_ids=seed_ids)
        assert result["validated_ids"] == seed_ids


# ── Done-signal short-circuit ────────────────────────────────────────────────

class _RaisingClient:
    """A fake client whose .messages.create raises on every call. Used to prove
    that the done-signal fast-path skipped the LLM entirely — if the LLM had been
    invoked, the test would fail with a RuntimeError instead of a clean PASS."""
    class _Messages:
        def create(self, **_kwargs):
            raise RuntimeError("LLM was invoked — fast-path failed to short-circuit")
    messages = _Messages()


@pytest.fixture
def block_llm():
    """Replace the agent's client with one that errors if invoked. Any test that
    triggers the LLM loop will fail loudly."""
    original = agent_mod._client
    agent_mod._client = _RaisingClient()
    yield
    agent_mod._client = original


def _cart_with_one_item() -> list[dict]:
    return [{
        "item_id":   "taco_birria",
        "name":      "Birria Taco",
        "price":     4.99,
        "quantity":  1,
        "modifiers": [],
        "modifier_upcharge": 0.0,
        "line_total": 4.99,
    }]


class TestDoneSignalDetection:
    """Pure-function tests on _is_pure_done_signal — no API, no DB."""

    @pytest.mark.parametrize("msg", [
        "that's all",
        "That's all",
        "THAT'S ALL",
        "that's all, thanks",
        "thanks, that's all",
        "that's it",
        "thats all",          # apostrophe stripped
        "thats it",
        "place it",
        "Place my order",
        "place order please",
        "place it, thanks!",
        "go ahead",
        "yes go ahead",
        "I'm done",
        "im done",
        "nothing else",
        "checkout",
        "check out",
        "confirm",
        "done",
    ])
    def test_recognized_done_signals(self, msg):
        assert agent_mod._is_pure_done_signal(msg) is True

    @pytest.mark.parametrize("msg", [
        "",
        " ",
        "hi",
        "hello there",
        "I'd like a taco",
        "Can you confirm the price?",            # 'confirm' present but not as the whole short msg
        "that's all but add a Coke",             # continuation 'but'
        "that's all wait actually I want guac",  # continuation 'wait'
        "place it instead with chicken",         # continuation 'instead'
        "okay that's all for now please thanks", # too long (>5 words)
        "Eso es todo",                           # Spanish — falls through to LLM
        "What's on the menu?",
    ])
    def test_rejected_non_signals(self, msg):
        assert agent_mod._is_pure_done_signal(msg) is False


class TestDoneSignalShortCircuit:
    """take_order must skip the LLM entirely when fast-path conditions are met."""

    def test_done_signal_with_items_fires_signal_checkout(self, block_llm):
        # If this test passes, the LLM was never invoked (the _RaisingClient
        # would have raised); signal_checkout fired deterministically.
        result = agent_mod.take_order(
            "that's all",
            current_cart=_cart_with_one_item(),
            validated_ids={"taco_birria"},
        )
        assert result["status"] == "checkout"
        assert "checkout" in result["agent_message"].lower()
        # Trace should have one tool call (signal_checkout) and zero LLM calls.
        assert result["trace"]["llm_calls"] == []
        assert len(result["trace"]["tool_calls"]) == 1
        assert result["trace"]["tool_calls"][0]["name"] == "signal_checkout"
        assert result["trace"]["tool_calls"][0]["error"] is False

    def test_done_signal_preserves_cart(self, block_llm):
        cart = _cart_with_one_item()
        result = agent_mod.take_order("that's all", current_cart=cart)
        assert result["cart"] == cart

    def test_done_signal_preserves_validated_ids(self, block_llm):
        seed = {"taco_birria", "drink_coke_mexican"}
        result = agent_mod.take_order(
            "place it", current_cart=_cart_with_one_item(), validated_ids=seed,
        )
        assert result["validated_ids"] == seed

    def test_empty_cart_falls_through_to_llm(self, reset_client):
        # With an empty cart, the fast-path must NOT short-circuit (signal_checkout
        # would error). The LLM should be invoked instead. Use a FakeClient that
        # raises a graceful exception so we can confirm the loop ran.
        agent_mod._client = _FakeClient(anthropic.APITimeoutError(request=_request()))
        result = agent_mod.take_order("that's all", current_cart=[])
        # Reached the LLM (timeout fallback proves it):
        assert result["status"] == "in_progress"
        assert any(c["stop_reason"] == "timeout" for c in result["trace"]["llm_calls"])

    def test_mixed_message_falls_through_to_llm(self, reset_client):
        agent_mod._client = _FakeClient(anthropic.APITimeoutError(request=_request()))
        result = agent_mod.take_order(
            "that's all, but also add a Coke",
            current_cart=_cart_with_one_item(),
        )
        # LLM was invoked (timeout in trace proves it):
        assert any(c["stop_reason"] == "timeout" for c in result["trace"]["llm_calls"])

    def test_hard_continuation_blocks_short_circuit(self, reset_client):
        # "but" / "wait" / "except" / "however" / "though" / "instead" are
        # strong reversal cues — even alongside a done phrase, they signal
        # the customer is about to add/change something. Must fall through.
        agent_mod._client = _FakeClient(anthropic.APITimeoutError(request=_request()))
        result = agent_mod.take_order(
            "that's all but add a Coke",
            current_cart=_cart_with_one_item(),
        )
        assert any(c["stop_reason"] == "timeout" for c in result["trace"]["llm_calls"])

    def test_soft_filler_actually_still_short_circuits(self, block_llm):
        # "actually that's all" is a customer changing their mind to be done —
        # the explicit done phrase wins. Real flow: tc_39 "Actually you know
        # what, just one is fine. Confirm it."
        result = agent_mod.take_order(
            "actually that's all",
            current_cart=_cart_with_one_item(),
        )
        assert result["status"] == "checkout"
        assert result["trace"]["llm_calls"] == []

    def test_non_signal_message_falls_through(self, reset_client):
        # A normal "add to cart" message must still go through the LLM.
        agent_mod._client = _FakeClient(anthropic.APITimeoutError(request=_request()))
        result = agent_mod.take_order(
            "I'd like another taco",
            current_cart=_cart_with_one_item(),
        )
        assert any(c["stop_reason"] == "timeout" for c in result["trace"]["llm_calls"])


# ── Contextual short-circuit: 'no' after 'anything else?' ────────────────────

class TestContextualDoneSignal:
    """Short negative messages ('no', 'nope') count as done signals when the
    previous assistant turn asked an 'anything else?' style completion prompt."""

    @pytest.mark.parametrize("prompt_text", [
        "Conchas added! Anything else?",
        "Birria Taco added — anything else, or are you good to go?",
        "Got it. Ready to check out?",
        "One Pollo Bowl added. Would you like anything else?",
        "Added. Is that everything?",
        "Done. Are you all set?",
    ])
    def test_no_after_completion_prompt_short_circuits(self, block_llm, prompt_text):
        history = [
            {"role": "user",      "content": "concha please"},
            {"role": "assistant", "content": prompt_text},
        ]
        result = agent_mod.take_order(
            "no",
            conversation_history=history,
            current_cart=_cart_with_one_item(),
        )
        assert result["status"] == "checkout"
        assert result["trace"]["llm_calls"] == []
        assert len(result["trace"]["tool_calls"]) == 1
        assert result["trace"]["tool_calls"][0]["name"] == "signal_checkout"

    @pytest.mark.parametrize("denial", ["no", "nope", "nah", "no thanks", "no thank you"])
    def test_various_negative_responses_short_circuit(self, block_llm, denial):
        history = [
            {"role": "user",      "content": "taco"},
            {"role": "assistant", "content": "Birria Taco added. Anything else?"},
        ]
        result = agent_mod.take_order(
            denial,
            conversation_history=history,
            current_cart=_cart_with_one_item(),
        )
        assert result["status"] == "checkout"
        assert result["trace"]["llm_calls"] == []

    def test_no_WITHOUT_completion_prompt_falls_through(self, reset_client):
        # If the previous turn didn't ask 'anything else?', a bare 'no' is
        # ambiguous (could be denying a clarification). LLM must handle.
        history = [
            {"role": "user",      "content": "i want birria"},
            {"role": "assistant", "content": "Did you mean the Birria Taco?"},
        ]
        agent_mod._client = _FakeClient(anthropic.APITimeoutError(request=_request()))
        result = agent_mod.take_order(
            "no",
            conversation_history=history,
            current_cart=_cart_with_one_item(),
        )
        # LLM was invoked — timeout proves it.
        assert any(c["stop_reason"] == "timeout" for c in result["trace"]["llm_calls"])

    def test_no_with_continuation_falls_through(self, reset_client):
        # "no, but add a coke" — even with completion prompt, the 'but' signals
        # the customer is about to add something. LLM must handle.
        history = [
            {"role": "user",      "content": "taco"},
            {"role": "assistant", "content": "Added. Anything else?"},
        ]
        agent_mod._client = _FakeClient(anthropic.APITimeoutError(request=_request()))
        result = agent_mod.take_order(
            "no but add a coke",
            conversation_history=history,
            current_cart=_cart_with_one_item(),
        )
        assert any(c["stop_reason"] == "timeout" for c in result["trace"]["llm_calls"])

    def test_no_with_empty_cart_falls_through(self, reset_client):
        # Even after a completion prompt, if the cart is empty 'no' means
        # 'I'm not ordering anything', not 'check me out with an empty cart'.
        history = [
            {"role": "user",      "content": "do you have anything spicy"},
            {"role": "assistant", "content": "Yes — anything to add?"},
        ]
        agent_mod._client = _FakeClient(anthropic.APITimeoutError(request=_request()))
        result = agent_mod.take_order(
            "no",
            conversation_history=history,
            current_cart=[],
        )
        assert any(c["stop_reason"] == "timeout" for c in result["trace"]["llm_calls"])

    def test_history_with_list_content_blocks(self, block_llm):
        # Anthropic-style assistant message stored as list of content blocks
        # (text + tool_use). Helper must still find the 'anything else?' text.
        history = [
            {"role": "user", "content": "taco"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Birria Taco added! Anything else?"},
            ]},
        ]
        result = agent_mod.take_order(
            "no",
            conversation_history=history,
            current_cart=_cart_with_one_item(),
        )
        assert result["status"] == "checkout"
        assert result["trace"]["llm_calls"] == []


class TestExpandedDonePhrases:
    """New explicit phrases added to _DONE_SIGNAL_PHRASES — should short-circuit
    on their own without needing prior context."""

    @pytest.mark.parametrize("msg", [
        "all good",
        "we're good",
        "were good",
        "that's everything",
        "thats everything",
        "we're good thanks",
    ])
    def test_new_done_phrases_short_circuit(self, block_llm, msg):
        result = agent_mod.take_order(
            msg, current_cart=_cart_with_one_item(),
        )
        assert result["status"] == "checkout"
        assert result["trace"]["llm_calls"] == []


# ── Cache control: tools and conversation history ────────────────────────────

class TestPromptCachingConfig:
    """The agent attaches Anthropic's cache_control breakpoints on three things:
    system prompt, the last tool definition, and the last assistant message in
    history. This locks the cache markers in place — a future refactor that
    drops them is a real cost regression."""

    def test_system_prompt_is_cache_controlled(self):
        # _SYSTEM is a list with one text block carrying cache_control.
        assert isinstance(agent_mod._SYSTEM, list) and agent_mod._SYSTEM
        block = agent_mod._SYSTEM[0]
        assert block.get("cache_control") == {"type": "ephemeral"}

    def test_last_tool_is_cache_controlled(self):
        # _TOOLS mirrors TOOL_SCHEMAS with cache_control attached to the last entry.
        assert agent_mod._TOOLS, "tool list must not be empty"
        last = agent_mod._TOOLS[-1]
        assert last.get("cache_control") == {"type": "ephemeral"}
        # Earlier tools should NOT carry the marker — only the last needs it
        # for Anthropic to cache the full block.
        for t in agent_mod._TOOLS[:-1]:
            assert "cache_control" not in t

    def test_tools_count_matches_schemas(self):
        # _TOOLS is built from TOOL_SCHEMAS — same length, same tool names.
        from agent.tool_schemas import TOOL_SCHEMAS
        assert len(agent_mod._TOOLS) == len(TOOL_SCHEMAS)
        assert [t["name"] for t in agent_mod._TOOLS] == [t["name"] for t in TOOL_SCHEMAS]


class TestHistoryCacheBreakpoint:
    """_with_history_cache_breakpoint marks the last assistant message in the
    messages list with cache_control. Tested as a pure function — no API."""

    def test_empty_messages_pass_through(self):
        assert agent_mod._with_history_cache_breakpoint([]) == []

    def test_user_only_no_breakpoint_added(self):
        # First turn — only a user message exists. Nothing to cache from history.
        msgs = [{"role": "user", "content": "hi"}]
        out  = agent_mod._with_history_cache_breakpoint(msgs)
        # No assistant message → result is unchanged (same content shape).
        assert out == msgs

    def test_last_assistant_string_content_gets_cache_control(self):
        msgs = [
            {"role": "user",      "content": "I want a taco"},
            {"role": "assistant", "content": "Got it! Birria Taco added."},
            {"role": "user",      "content": "thanks"},
        ]
        out = agent_mod._with_history_cache_breakpoint(msgs)
        # The assistant message at index 1 has been transformed to a structured list.
        asst = out[1]
        assert isinstance(asst["content"], list)
        assert asst["content"][0]["type"] == "text"
        assert asst["content"][0]["text"] == "Got it! Birria Taco added."
        assert asst["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_input_is_not_mutated(self):
        msgs = [
            {"role": "user",      "content": "x"},
            {"role": "assistant", "content": "y"},
        ]
        _ = agent_mod._with_history_cache_breakpoint(msgs)
        # The caller's list and dicts must be unchanged.
        assert msgs[1]["content"] == "y"
        assert isinstance(msgs[1]["content"], str)

    def test_last_of_multiple_assistant_messages_is_marked(self):
        # In a multi-turn conversation, only the most recent assistant message
        # gets the marker. Earlier assistant messages stay as-is.
        msgs = [
            {"role": "user",      "content": "1"},
            {"role": "assistant", "content": "first"},
            {"role": "user",      "content": "2"},
            {"role": "assistant", "content": "second"},
            {"role": "user",      "content": "3"},
        ]
        out = agent_mod._with_history_cache_breakpoint(msgs)
        # First assistant unchanged.
        assert out[1]["content"] == "first"
        # Second assistant converted + cache_control attached.
        assert isinstance(out[3]["content"], list)
        assert out[3]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_list_content_blocks_are_dict_serialized(self):
        # Simulate an assistant message whose content is already a list of
        # structured blocks (mid-loop responses look like this).
        msgs = [
            {"role": "user", "content": "i want a taco"},
            {"role": "assistant", "content": [
                {"type": "text", "text": "Searching..."},
                {"type": "tool_use", "id": "x", "name": "search_menu",
                 "input": {"query": "taco"}},
            ]},
        ]
        out = agent_mod._with_history_cache_breakpoint(msgs)
        blocks = out[1]["content"]
        assert isinstance(blocks, list) and len(blocks) == 2
        # First block unchanged.
        assert "cache_control" not in blocks[0]
        # Last block has cache_control attached.
        assert blocks[-1]["cache_control"] == {"type": "ephemeral"}
        assert blocks[-1]["name"] == "search_menu"

    def test_sdk_content_block_objects_convert_cleanly(self):
        # When Anthropic SDK objects come back as response.content, the helper
        # must convert them via model_dump() rather than choking.
        class _FakeBlock:
            def __init__(self, **kw): self._d = kw
            def model_dump(self, exclude_none=True):
                return {k: v for k, v in self._d.items() if v is not None}
        msgs = [
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": [
                _FakeBlock(type="text", text="ok"),
                _FakeBlock(type="tool_use", id="t1", name="search_menu",
                           input={"query": "x"}),
            ]},
        ]
        out = agent_mod._with_history_cache_breakpoint(msgs)
        blocks = out[1]["content"]
        assert isinstance(blocks, list)
        assert all(isinstance(b, dict) for b in blocks)
        assert blocks[-1]["cache_control"] == {"type": "ephemeral"}


# ── FAQ short-circuit ────────────────────────────────────────────────────────

class TestFaqDetection:
    """Pure-function tests on _is_faq_query — no API, no DB."""

    @pytest.mark.parametrize("msg", [
        # Hours / open status
        "what time do you close",
        "what time are you open",
        "what hours do you have",
        "are you open",
        "are you open today",
        "when do you close",
        "when do you open",
        # Location / contact
        "where are you located",
        "where are you",
        "what's your address",
        "your phone number",
        # Amenities
        "do you have parking",
        "is there parking",
        "do you have wifi",
        # Service options
        "do you deliver",
        "do you do delivery",
        "do you cater",
        "do you take reservations",
        # Payment
        "do you take credit cards",
        "do you accept cash",
        # Dietary
        "do you have vegan options",
        "do you have gluten free options",
        "what's vegan here",
    ])
    def test_recognized_faq_queries(self, msg):
        assert agent_mod._is_faq_query(msg) is True

    @pytest.mark.parametrize("msg", [
        "",
        " ",
        "hi",
        "hello",
        # Ordering intent — blocker words present
        "I want a taco",
        "can I get a birria taco",
        "give me a quesadilla",
        "I'll have a Mexican Coke",
        # Menu items mentioned — could be ordering, not FAQ
        "what's a birria taco",
        "is the birria taco spicy",
        "do you have spicy tacos",
        # Generic questions that don't match FAQ triggers
        "what's good here",
        "what do you recommend",
        # Too long — likely contains nuance the LLM should handle
        "hey there my friend it has been a while since I last visited where are you located again",
        # Done signals
        "that's all",
        "no",
    ])
    def test_rejected_non_faq_queries(self, msg):
        assert agent_mod._is_faq_query(msg) is False


class TestFaqShortCircuit:
    """take_order must skip the LLM when the message is an unambiguous FAQ
    query AND search_faq returns a confident match."""

    def test_hours_query_short_circuits(self, block_llm):
        result = agent_mod.take_order("what time do you close")
        assert result["status"] == "in_progress"
        # Trace should show one tool call (search_faq) and zero LLM calls.
        assert result["trace"]["llm_calls"] == []
        assert len(result["trace"]["tool_calls"]) == 1
        assert result["trace"]["tool_calls"][0]["name"] == "search_faq"

    def test_parking_query_short_circuits(self, block_llm):
        result = agent_mod.take_order("do you have parking")
        assert result["trace"]["llm_calls"] == []
        # Answer should contain something useful — non-empty.
        assert result["agent_message"]

    def test_address_query_short_circuits(self, block_llm):
        result = agent_mod.take_order("where are you located")
        assert result["trace"]["llm_calls"] == []

    def test_cart_is_preserved_across_faq_short_circuit(self, block_llm):
        # Customer with a cart asks an FAQ — cart must survive untouched.
        cart = _cart_with_one_item()
        result = agent_mod.take_order("what time do you close", current_cart=cart)
        assert result["cart"] == cart
        assert result["status"] == "in_progress"

    def test_validated_ids_preserved_across_faq_short_circuit(self, block_llm):
        seed = {"taco_birria", "drink_coke_mexican"}
        result = agent_mod.take_order("are you open", validated_ids=seed)
        assert result["validated_ids"] == seed

    def test_menu_question_falls_through_to_llm(self, reset_client):
        # "what's a birria taco" should NOT short-circuit — it's a menu Q,
        # not a restaurant-fact Q. LLM should handle (using a graceful timeout
        # fallback here to prove the LLM was invoked).
        agent_mod._client = _FakeClient(anthropic.APITimeoutError(request=_request()))
        result = agent_mod.take_order("what's a birria taco")
        assert any(c["stop_reason"] == "timeout" for c in result["trace"]["llm_calls"])

    def test_ordering_message_falls_through_to_llm(self, reset_client):
        # Even if the message has FAQ-shape phrases, ordering intent blocks short-circuit.
        agent_mod._client = _FakeClient(anthropic.APITimeoutError(request=_request()))
        result = agent_mod.take_order("I want a taco and what time do you close")
        assert any(c["stop_reason"] == "timeout" for c in result["trace"]["llm_calls"])

    def test_unknown_faq_falls_through_to_llm(self, reset_client):
        # Trigger phrase matches but search_faq has no good answer — fall
        # through to LLM rather than serving the canned "I don't have info"
        # response. The LLM might interpret the message differently.
        # Here we use a question that matches a phrase but is too vague to
        # land confidently on any FAQ entry.
        agent_mod._client = _FakeClient(anthropic.APITimeoutError(request=_request()))
        # Patch search_faq to return found=False to force the fall-through.
        import agent.agent as a
        original_search_faq = a.search_faq
        try:
            a.search_faq = lambda q: {"found": False, "answer": "...", "question": "", "confidence": "low"}
            result = agent_mod.take_order("are you open")
            assert any(c["stop_reason"] == "timeout" for c in result["trace"]["llm_calls"])
        finally:
            a.search_faq = original_search_faq


# ── Token-usage observability ────────────────────────────────────────────────

class TestExtractUsage:
    """_extract_usage normalises Anthropic's SDK Usage object into a plain dict
    with four well-known fields. Lets the rest of the system compute real cost."""

    def test_none_input_returns_none(self):
        assert agent_mod._extract_usage(None) is None

    def test_pydantic_style_usage_via_model_dump(self):
        class _U:
            def model_dump(self, exclude_none=True):
                return {
                    "input_tokens": 1234,
                    "output_tokens": 56,
                    "cache_creation_input_tokens": 200,
                    "cache_read_input_tokens": 4000,
                }
        u = agent_mod._extract_usage(_U())
        assert u == {
            "input_tokens": 1234,
            "output_tokens": 56,
            "cache_creation_input_tokens": 200,
            "cache_read_input_tokens": 4000,
        }

    def test_dict_passthrough(self):
        # Already a dict — handled too (covers tests that monkeypatch responses).
        u = agent_mod._extract_usage({"input_tokens": 10, "output_tokens": 5})
        assert u["input_tokens"] == 10
        assert u["output_tokens"] == 5
        # Missing fields default to 0.
        assert u["cache_creation_input_tokens"] == 0
        assert u["cache_read_input_tokens"] == 0

    def test_object_with_attrs_fallback(self):
        # No model_dump and no dict — fall back to attribute reads.
        class _U:
            input_tokens = 100
            output_tokens = 20
            cache_creation_input_tokens = 0
            cache_read_input_tokens = 500
        u = agent_mod._extract_usage(_U())
        assert u["input_tokens"] == 100
        assert u["cache_read_input_tokens"] == 500

    def test_returned_fields_are_int_typed(self):
        u = agent_mod._extract_usage({"input_tokens": "42"})  # string sneaks through
        assert isinstance(u["input_tokens"], int)
        assert u["input_tokens"] == 42


class TestTraceUsageCapture:
    """Trace.record_llm stores usage in its serialised output when provided."""

    def test_record_without_usage_omits_field(self):
        from agent.tracing import Trace
        t = Trace()
        t.record_llm(1, 123.4, "end_turn")
        assert t.llm_calls[0] == {
            "iteration": 1, "latency_ms": 123.4, "stop_reason": "end_turn",
        }
        # No usage key when none provided.
        assert "usage" not in t.llm_calls[0]

    def test_record_with_usage_attaches_dict(self):
        from agent.tracing import Trace
        t = Trace()
        t.record_llm(
            2, 200.0, "tool_use",
            usage={
                "input_tokens": 500,
                "output_tokens": 50,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 3000,
            },
        )
        u = t.llm_calls[0]["usage"]
        assert u["input_tokens"] == 500
        assert u["cache_read_input_tokens"] == 3000

    def test_usage_survives_to_dict_serialization(self):
        from agent.tracing import Trace
        t = Trace()
        t.record_llm(1, 100.0, "end_turn", usage={"input_tokens": 10, "output_tokens": 2})
        d = t.to_dict()
        assert d["llm_calls"][0]["usage"]["input_tokens"] == 10


class TestEvalUsageAggregation:
    """The eval runner sums token usage across every LLM call in every turn
    and includes the aggregate in --output JSON. Verifies the sum + cache-hit
    rate computation without running the live eval."""

    def test_aggregates_across_results(self):
        from evals.run_evals import _aggregate_token_usage
        from evals.metrics import CaseResult, TurnResult

        def _turn(usages):
            return TurnResult(
                turn_idx=0, user_message="x", agent_message="y", status="checkout",
                order=None, latency_ms=10.0,
                trace={"llm_calls": [{"iteration": i+1, "latency_ms": 100,
                                       "stop_reason": "tool_use", "usage": u}
                                     for i, u in enumerate(usages)]},
            )

        results = [
            CaseResult(case_id="c1", title="t1", category="simple", passed=True,
                       turns=[_turn([
                           {"input_tokens": 100, "output_tokens": 10,
                            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 500},
                           {"input_tokens": 50,  "output_tokens": 20,
                            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 700},
                       ])]),
            CaseResult(case_id="c2", title="t2", category="simple", passed=True,
                       turns=[_turn([
                           {"input_tokens": 200, "output_tokens": 30,
                            "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 0},
                       ])]),
        ]
        agg = _aggregate_token_usage(results)
        assert agg["llm_calls"] == 3
        assert agg["llm_calls_with_usage"] == 3
        assert agg["input_tokens"] == 350
        assert agg["output_tokens"] == 60
        assert agg["cache_creation_input_tokens"] == 1000
        assert agg["cache_read_input_tokens"] == 1200
        # hit_rate = 1200 / (350 + 1000 + 1200) = 0.4706
        assert abs(agg["cache_hit_rate"] - 0.4706) < 0.001
        assert agg["estimated_cost_usd_haiku"] > 0

    def test_handles_traces_without_usage(self):
        # Older traces (pre-this-fix) or short-circuit responses have llm_calls
        # without usage. Aggregator must count them but skip the sum.
        from evals.run_evals import _aggregate_token_usage
        from evals.metrics import CaseResult, TurnResult

        turn = TurnResult(
            turn_idx=0, user_message="x", agent_message="y", status="checkout",
            order=None, latency_ms=10.0,
            trace={"llm_calls": [{"iteration": 1, "latency_ms": 100,
                                   "stop_reason": "end_turn"}]},  # no 'usage'
        )
        result = CaseResult(case_id="c1", title="t", category="simple",
                            passed=True, turns=[turn])
        agg = _aggregate_token_usage([result])
        assert agg["llm_calls"] == 1
        assert agg["llm_calls_with_usage"] == 0
        assert agg["input_tokens"] == 0
        assert agg["cache_hit_rate"] == 0.0

    def test_empty_results_return_zero_block(self):
        from evals.run_evals import _aggregate_token_usage
        agg = _aggregate_token_usage([])
        assert agg["llm_calls"] == 0
        assert agg["cache_hit_rate"] == 0.0
        assert agg["estimated_cost_usd_haiku"] == 0.0
