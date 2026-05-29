"""
Unit tests for the three tool functions. No API calls — pure DB logic.
Run: python -m pytest tests/test_tools.py -v
"""

import pytest
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from db.setup import init_db
from agent.tools import (
    _CartCtx,
    add_to_cart,
    get_cart_contents,
    get_item_details,
    place_order,
    remove_from_cart,
    search_menu,
    set_item_quantity,
    signal_checkout,
    update_item_modifiers,
)


@pytest.fixture(scope="session", autouse=True)
def db():
    init_db()


class TestSearchMenu:
    def test_exact_name_match(self):
        # At 10k-item scale many birria-style tacos exist; the original "Birria Taco"
        # should always be the top-ranked candidate.
        result = search_menu("birria taco")
        assert result["match"] in ("exact", "ambiguous")
        assert result["items"][0]["id"] == "taco_birria"

    def test_category_search(self):
        result = search_menu("drinks")
        assert result["match"] in ("exact", "ambiguous")
        assert len(result["items"]) > 0

    def test_dietary_vegan(self):
        result = search_menu("vegan")
        assert len(result["items"]) > 0

    def test_typo_recovery(self):
        # "tcao" → fuzzy-matches "taco"; original "Birria Taco" must be top candidate.
        result = search_menu("birria tcao")
        assert result["match"] in ("exact", "ambiguous"), "Should fuzzy-match despite typo"
        assert result["items"][0]["id"] == "taco_birria"

    def test_empty_query_returns_none(self):
        result = search_menu("")
        assert result["match"] == "none"
        assert result["items"] == []
        assert result["top_item"] is None

    def test_ambiguous_results_capped(self):
        # Ambiguous results are capped at 5 to keep conversation history lean
        # and the disambiguation question manageable for the customer.
        result = search_menu("taco")
        assert len(result["items"]) <= 5

    def test_not_on_menu_returns_none(self):
        result = search_menu("pepperoni pizza")
        # At 10k scale, hybrid/semantic search may surface related items as "ambiguous"
        # rather than a hard "none". The real invariant: no pizza items ever returned.
        assert result["match"] in ("none", "ambiguous")
        assert all("pizza" not in r["name"].lower() for r in result["items"])

    def test_result_items_have_required_fields(self):
        result = search_menu("chicken bowl")
        assert len(result["items"]) > 0
        for r in result["items"]:
            assert "id" in r
            assert "name" in r
            assert "price" in r
            assert "available" in r

    def test_ambiguous_returns_multiple_items(self):
        # "I want a burrito" — multiple burritos, no distinguishing word
        result = search_menu("burrito")
        assert result["match"] == "ambiguous"
        assert len(result["items"]) > 1
        assert result["top_item"] is None

    def test_descriptor_match_resolves_exactly(self):
        # "cheese quesadilla" — "cheese" is the descriptor that picks one item
        result = search_menu("cheese quesadilla")
        assert result["match"] == "exact"
        assert result["top_item"]["id"] == "quesadilla_cheese"


class TestSearchClearWinner:
    """The fuzzy-match fast-path: one item's name is a strong match AND clearly
    ahead of the runner-up → return exact, no clarification needed. Applies
    uniformly across the 10k+ catalog regardless of canonical vs synthetic."""

    def test_canonical_birria_taco_wins_over_synthetic_variants(self):
        # The 10k expanded catalog has many "Birria * Taco" variants. The
        # canonical "Birria Taco" should still win on a bare "birria taco" query.
        r = search_menu("birria taco")
        assert r["match"] == "exact"
        assert r["top_item"]["id"] == "taco_birria"

    def test_plural_form_still_resolves(self):
        # "birria tacos" (plural) is one character off — should still resolve.
        r = search_menu("birria tacos")
        assert r["match"] == "exact"
        assert r["top_item"]["id"] == "taco_birria"

    def test_capitalization_does_not_block_match(self):
        r = search_menu("BIRRIA TACO")
        assert r["match"] == "exact"
        assert r["top_item"]["id"] == "taco_birria"

    def test_carne_asada_taco_wins(self):
        # Same pattern as birria — canonical wins over expanded variants.
        r = search_menu("carne asada taco")
        assert r["match"] == "exact"
        assert r["top_item"]["id"] == "taco_carne_asada"

    def test_mexican_coke_resolves(self):
        # Customer phrasing differs from canonical name ("Mexican Coca-Cola")
        # but the semantic + fuzzy combination should still pick it as a clear
        # winner over any synthetic drink variants.
        r = search_menu("Mexican Coke")
        assert r["match"] in ("exact", "ambiguous")
        # Whichever match flag — Mexican Coca-Cola must be the top suggestion.
        top = r.get("top_item") or (r["items"][0] if r["items"] else None)
        assert top is not None
        assert top["id"] == "drink_coke_mexican"

    def test_single_word_birria_stays_ambiguous(self):
        # "birria" alone is genuinely ambiguous — could be taco, bowl, or burrito.
        # No item's name should win by a clear margin.
        r = search_menu("birria")
        assert r["match"] == "ambiguous"

    def test_generic_burrito_stays_ambiguous(self):
        # 4 canonical burritos all match similarly — must keep asking.
        r = search_menu("I want a burrito")
        assert r["match"] == "ambiguous"
        assert len(r["items"]) > 1

    def test_clear_winner_does_not_override_descriptor_match(self):
        # "cheese quesadilla" should still resolve via descriptor matching
        # (cheese is unique). Adding the clear-winner check shouldn't break this.
        r = search_menu("cheese quesadilla")
        assert r["match"] == "exact"
        assert r["top_item"]["id"] == "quesadilla_cheese"

    def test_informal_query_chips_and_guac_resolves(self):
        # Regression for tc_05: "chips and guac" should resolve to "Chips &
        # Guacamole" via partial_ratio (substring match), not asking "regular
        # or loaded?". token_sort_ratio alone would have scored 71 here.
        r = search_menu("chips and guac")
        assert r["match"] == "exact"
        assert r["top_item"]["id"] == "side_chips_guac"

    def test_chips_and_guacamole_full_name(self):
        # The full canonical phrasing should also resolve cleanly.
        r = search_menu("chips and guacamole")
        assert r["match"] == "exact"
        assert r["top_item"]["id"] == "side_chips_guac"

    def test_long_spanish_query_resolves(self):
        # Regression for tc_45: "Dame dos tacos de carne asada" should resolve
        # to canonical Carne Asada Taco via token_set_ratio / partial_ratio.
        r = search_menu("dame dos tacos de carne asada")
        assert r["match"] == "exact"
        assert r["top_item"]["id"] == "taco_carne_asada"

    def test_query_with_extra_words_resolves(self):
        # Regression for tc_85 / tc_88: "I want a dozen birria tacos place it"
        # has the canonical name as a substring. partial_ratio = 100 picks it.
        r = search_menu("I want a dozen birria tacos place it")
        assert r["match"] == "exact"
        assert r["top_item"]["id"] == "taco_birria"


class TestGetItemDetails:
    def test_known_item(self):
        item = get_item_details("taco_birria")
        assert item["id"] == "taco_birria"
        assert item["price"] == 4.99
        assert item["available"] is True
        assert isinstance(item["modifiers"], list)
        assert isinstance(item["options"], dict)

    def test_out_of_stock_item(self):
        item = get_item_details("taco_shrimp")
        assert item["available"] is False

    def test_unknown_item_returns_error(self):
        result = get_item_details("fake_item_xyz")
        assert "error" in result

    def test_required_option_present(self):
        item = get_item_details("burrito_build_your_own")
        assert "protein" in item["options"]
        assert item["options"]["protein"]["required"] is True

    def test_modifiers_have_price(self):
        item = get_item_details("bowl_pollo")
        add_guac = next((m for m in item["modifiers"] if m["id"] == "add_guac"), None)
        assert add_guac is not None
        assert add_guac["price"] == 1.5


class TestPlaceOrder:
    def test_single_item_subtotal(self):
        result = place_order([{"item_id": "taco_birria", "quantity": 1}])
        assert result["subtotal"] == 4.99
        assert result["order_id"].startswith("TT-")
        assert len(result["items"]) == 1
        assert result["items"][0]["name"] == "Birria Taco"

    def test_quantity_multiplied(self):
        result = place_order([{"item_id": "taco_birria", "quantity": 2}])
        assert result["subtotal"] == 9.98
        assert result["items"][0]["quantity"] == 2
        assert result["items"][0]["line_total"] == 9.98

    def test_modifier_adds_to_price(self):
        result = place_order([{
            "item_id": "bowl_pollo",
            "quantity": 1,
            "modifiers": ["add_guac"],
        }])
        assert result["subtotal"] == pytest.approx(13.99, abs=0.01)

    def test_multi_item_subtotal(self):
        result = place_order([
            {"item_id": "taco_birria", "quantity": 2},
            {"item_id": "drink_coke_mexican", "quantity": 1},
        ])
        # 2×4.99 + 3.49 = 13.47
        assert result["subtotal"] == pytest.approx(13.47, abs=0.01)

    def test_unknown_item_handled(self):
        result = place_order([{"item_id": "does_not_exist", "quantity": 1}])
        # Should not crash — error item gets $0 line_total
        assert result["subtotal"] == 0.0

    def test_special_instructions_passed_through(self):
        result = place_order(
            [{"item_id": "taco_birria", "quantity": 1}],
            special_instructions="everything in one bag",
        )
        assert result["special_instructions"] == "everything in one bag"

    def test_unique_order_ids(self):
        r1 = place_order([{"item_id": "taco_birria", "quantity": 1}])
        r2 = place_order([{"item_id": "taco_birria", "quantity": 1}])
        assert r1["order_id"] != r2["order_id"]


class TestCartTools:
    def test_add_to_cart_single_item(self):
        ctx = _CartCtx([])
        result = add_to_cart("taco_birria", 2, [], ctx)
        assert "error" not in result
        assert result["added"] == "Birria Taco"
        assert result["quantity"] == 2
        assert len(ctx.cart) == 1
        assert ctx.cart[0]["line_total"] == pytest.approx(9.98, abs=0.01)

    def test_add_to_cart_increments_existing(self):
        ctx = _CartCtx([])
        add_to_cart("taco_birria", 1, [], ctx)
        add_to_cart("taco_birria", 1, [], ctx)
        assert len(ctx.cart) == 1
        assert ctx.cart[0]["quantity"] == 2

    def test_add_to_cart_price_from_db(self):
        ctx = _CartCtx([])
        result = add_to_cart("taco_birria", 1, [], ctx)
        assert ctx.cart[0]["price"] == 4.99

    def test_remove_from_cart(self):
        ctx = _CartCtx([])
        add_to_cart("taco_birria", 1, [], ctx)
        add_to_cart("drink_coke_mexican", 1, [], ctx)
        assert len(ctx.cart) == 2
        remove_from_cart("taco_birria", ctx)
        assert len(ctx.cart) == 1
        assert ctx.cart[0]["item_id"] == "drink_coke_mexican"

    def test_get_cart_contents(self):
        ctx = _CartCtx([])
        add_to_cart("taco_birria", 2, [], ctx)
        contents = get_cart_contents(ctx)
        assert contents["count"] == 2
        assert contents["subtotal"] == pytest.approx(9.98, abs=0.01)
        assert len(contents["items"]) == 1

    def test_signal_checkout_succeeds_with_items(self):
        ctx = _CartCtx([])
        add_to_cart("taco_birria", 1, [], ctx)
        result = signal_checkout(ctx)
        assert result.get("ready") is True
        assert ctx.checkout_signaled is True

    def test_signal_checkout_fails_empty_cart(self):
        ctx = _CartCtx([])
        result = signal_checkout(ctx)
        assert "error" in result
        assert ctx.checkout_signaled is False


class TestCartModifierPricing:
    """Modifier upcharges (add_guac, extra_meat, etc.) must flow into line_total and subtotal."""

    def test_single_paid_modifier(self):
        # bowl_pollo $12.49 + add_guac $1.50 = $13.99
        ctx = _CartCtx([])
        result = add_to_cart("bowl_pollo", 1, ["add_guac"], ctx)
        assert result["modifier_upcharge"] == pytest.approx(1.50, abs=0.01)
        assert result["line_total"] == pytest.approx(13.99, abs=0.01)
        assert ctx.cart[0]["line_total"] == pytest.approx(13.99, abs=0.01)
        assert get_cart_contents(ctx)["subtotal"] == pytest.approx(13.99, abs=0.01)

    def test_multiple_paid_modifiers(self):
        # burrito_california $13.49 + extra_meat $3.00 + add_sour_cream $0.50 = $16.99
        ctx = _CartCtx([])
        result = add_to_cart("burrito_california", 1, ["extra_meat", "add_sour_cream"], ctx)
        assert result["modifier_upcharge"] == pytest.approx(3.50, abs=0.01)
        assert result["line_total"] == pytest.approx(16.99, abs=0.01)
        assert get_cart_contents(ctx)["subtotal"] == pytest.approx(16.99, abs=0.01)

    def test_paid_modifier_with_quantity(self):
        # 2 × (bowl_pollo $12.49 + add_guac $1.50) = $27.98
        ctx = _CartCtx([])
        result = add_to_cart("bowl_pollo", 2, ["add_guac"], ctx)
        assert result["line_total"] == pytest.approx(27.98, abs=0.01)
        assert get_cart_contents(ctx)["subtotal"] == pytest.approx(27.98, abs=0.01)

    def test_free_modifier_does_not_change_price(self):
        # taco_birria $4.99 with no_cilantro ($0) = $4.99
        ctx = _CartCtx([])
        result = add_to_cart("taco_birria", 1, ["no_cilantro"], ctx)
        assert result["modifier_upcharge"] == pytest.approx(0.0, abs=0.01)
        assert result["line_total"] == pytest.approx(4.99, abs=0.01)

    def test_mixed_paid_and_free_modifiers(self):
        # taco_birria $4.99 + add_cheese $0.75 + no_onion $0 = $5.74
        ctx = _CartCtx([])
        result = add_to_cart("taco_birria", 1, ["add_cheese", "no_onion"], ctx)
        assert result["modifier_upcharge"] == pytest.approx(0.75, abs=0.01)
        assert result["line_total"] == pytest.approx(5.74, abs=0.01)

    def test_unknown_modifier_returns_error(self):
        # A hallucinated modifier ID must surface as an error so the agent
        # self-corrects — silently dropping unknown modifiers to $0 upcharge
        # would let the customer pay $0 for a $3 modifier when the LLM
        # invents an ID like 'add_extra_meat' (real ID is 'extra_meat').
        ctx = _CartCtx([])
        result = add_to_cart("taco_birria", 1, ["ghost_modifier"], ctx)
        assert "error" in result
        assert "ghost_modifier" in result["error"]
        assert "Valid modifiers" in result["error"]
        # Nothing got added to the cart on the error path.
        assert ctx.cart == []

    def test_same_item_different_modifiers_split_into_two_lines(self):
        # Bowl with guac vs. bowl with nothing must be separate cart entries (different totals).
        ctx = _CartCtx([])
        add_to_cart("bowl_pollo", 1, ["add_guac"], ctx)
        add_to_cart("bowl_pollo", 1, [], ctx)
        assert len(ctx.cart) == 2
        subtotal = get_cart_contents(ctx)["subtotal"]
        # 13.99 + 12.49 = 26.48
        assert subtotal == pytest.approx(26.48, abs=0.01)

    def test_same_item_same_modifiers_increments_quantity(self):
        # Two adds of the same item+modifier list should consolidate into one line.
        ctx = _CartCtx([])
        add_to_cart("bowl_pollo", 1, ["add_guac"], ctx)
        add_to_cart("bowl_pollo", 1, ["add_guac"], ctx)
        assert len(ctx.cart) == 1
        assert ctx.cart[0]["quantity"] == 2
        assert ctx.cart[0]["line_total"] == pytest.approx(27.98, abs=0.01)

    def test_cart_subtotal_matches_place_order_subtotal(self):
        # The cart subtotal (live path) and place_order subtotal (test util) must agree
        # on items with paid modifiers — they used to diverge.
        ctx = _CartCtx([])
        add_to_cart("bowl_pollo", 1, ["add_guac"], ctx)
        cart_subtotal = get_cart_contents(ctx)["subtotal"]
        po = place_order([{"item_id": "bowl_pollo", "quantity": 1, "modifiers": ["add_guac"]}])
        assert cart_subtotal == pytest.approx(po["subtotal"], abs=0.01)


class TestAddToCartOptions:
    """Per-line options preservation. Closes the 'three tacos, three salsas'
    eval gap where distinct option choices were collapsed into a single line."""

    def test_same_item_different_options_split_into_separate_lines(self):
        # Three birria tacos with three different salsa choices → 3 cart lines.
        ctx = _CartCtx([])
        add_to_cart("taco_birria", 1, [], ctx, options={"salsa": "hot"})
        add_to_cart("taco_birria", 1, [], ctx, options={"salsa": "mild"})
        add_to_cart("taco_birria", 1, [], ctx, options={"salsa": "habanero"})
        assert len(ctx.cart) == 3
        salsas = sorted(e["options"]["salsa"] for e in ctx.cart)
        assert salsas == ["habanero", "hot", "mild"]
        # Subtotal: 3 × $4.99 = $14.97
        assert get_cart_contents(ctx)["subtotal"] == pytest.approx(14.97, abs=0.01)

    def test_same_item_same_options_increments_quantity(self):
        # Two adds with identical options stay merged into one line.
        ctx = _CartCtx([])
        add_to_cart("taco_birria", 1, [], ctx, options={"salsa": "hot"})
        add_to_cart("taco_birria", 1, [], ctx, options={"salsa": "hot"})
        assert len(ctx.cart) == 1
        assert ctx.cart[0]["quantity"] == 2
        assert ctx.cart[0]["options"] == {"salsa": "hot"}

    def test_no_options_dedup_unchanged(self):
        # Existing behavior preserved: no-options + no-options merges.
        ctx = _CartCtx([])
        add_to_cart("taco_birria", 1, [], ctx)
        add_to_cart("taco_birria", 1, [], ctx)
        assert len(ctx.cart) == 1
        assert ctx.cart[0]["quantity"] == 2

    def test_options_vs_no_options_split(self):
        # An order with no salsa specified and one with explicit salsa are different lines.
        ctx = _CartCtx([])
        add_to_cart("taco_birria", 1, [], ctx)
        add_to_cart("taco_birria", 1, [], ctx, options={"salsa": "hot"})
        assert len(ctx.cart) == 2

    def test_unknown_option_key_returns_error(self):
        # Hallucinated option key must surface as an error.
        ctx = _CartCtx([])
        result = add_to_cart("taco_birria", 1, [], ctx, options={"sauce_level": "hot"})
        assert "error" in result
        assert "sauce_level" in result["error"]
        assert ctx.cart == []

    def test_invalid_option_choice_returns_error(self):
        # Option key is valid but value isn't in the choices list.
        ctx = _CartCtx([])
        result = add_to_cart("taco_birria", 1, [], ctx, options={"salsa": "nuclear"})
        assert "error" in result
        assert "nuclear" in result["error"]
        assert ctx.cart == []

    def test_options_combined_with_modifiers_in_dedup(self):
        # Same item + same modifiers + different options → split.
        # Same item + different modifiers + same options → also split.
        ctx = _CartCtx([])
        add_to_cart("taco_birria", 1, ["add_cheese"], ctx, options={"salsa": "hot"})
        add_to_cart("taco_birria", 1, ["add_cheese"], ctx, options={"salsa": "mild"})
        add_to_cart("taco_birria", 1, [],             ctx, options={"salsa": "hot"})
        assert len(ctx.cart) == 3

    def test_cart_summary_text_surfaces_options(self):
        # Multiple lines for the same item with different options must be
        # visibly distinguishable in the summary string — otherwise the agent
        # sees three identical entries and loses situational awareness.
        from db.cart import cart_summary_text
        ctx = _CartCtx([])
        add_to_cart("taco_birria", 1, [], ctx, options={"salsa": "hot"})
        add_to_cart("taco_birria", 1, [], ctx, options={"salsa": "mild"})
        summary = cart_summary_text(ctx.cart)
        assert "salsa: hot" in summary
        assert "salsa: mild" in summary

    def test_cart_summary_text_no_options_unchanged(self):
        # Cart entries with no options shouldn't grow trailing brackets.
        from db.cart import cart_summary_text
        ctx = _CartCtx([])
        add_to_cart("taco_birria", 1, [], ctx)
        summary = cart_summary_text(ctx.cart)
        assert "[" not in summary
        assert "Birria Taco" in summary


class TestAddToCartReAddHint:
    """When add_to_cart matches an existing line, the response must surface
    an `already_in_cart` flag with the previous quantity and a hint pointing
    at set_item_quantity. This lets the agent detect unintentional re-adds
    (a recurring failure mode on clarification turns) and self-correct."""

    def test_first_add_marks_new_line(self):
        ctx = _CartCtx([])
        result = add_to_cart("taco_birria", 1, [], ctx)
        assert "error" not in result
        assert result["new_line"] is True
        assert "already_in_cart" not in result
        assert "hint" not in result

    def test_second_add_flags_already_in_cart(self):
        ctx = _CartCtx([])
        add_to_cart("taco_birria", 1, [], ctx)
        result = add_to_cart("taco_birria", 1, [], ctx)
        assert "error" not in result
        assert result["new_line"] is False
        assert result["already_in_cart"] is True
        assert result["previous_quantity"] == 1
        assert "set_item_quantity" in result["hint"]
        assert "taco_birria" in result["hint"]
        # Cart still incremented — defense layer is advisory, doesn't block.
        assert ctx.cart[0]["quantity"] == 2

    def test_different_options_does_not_trigger_hint(self):
        # Different option choice → separate line, NOT a re-add.
        ctx = _CartCtx([])
        add_to_cart("taco_birria", 1, [], ctx, options={"salsa": "hot"})
        result = add_to_cart("taco_birria", 1, [], ctx, options={"salsa": "mild"})
        assert result["new_line"] is True
        assert "already_in_cart" not in result

    def test_different_modifiers_does_not_trigger_hint(self):
        ctx = _CartCtx([])
        add_to_cart("bowl_pollo", 1, ["add_guac"], ctx)
        result = add_to_cart("bowl_pollo", 1, [], ctx)
        assert result["new_line"] is True
        assert "already_in_cart" not in result


class TestPerLineTargeting:
    """When per-line options create multiple cart lines for the same item_id,
    the line-targeting tools (remove / update_modifiers / set_quantity) must
    take an `options` arg to pick a specific line — otherwise the call fails
    cleanly with a listing of the existing lines, rather than silently
    mutating the first one."""

    def _three_birrias(self):
        from agent.tools import add_to_cart  # local to dodge re-import in some envs
        ctx = _CartCtx([])
        add_to_cart("taco_birria", 1, [], ctx, options={"salsa": "hot"})
        add_to_cart("taco_birria", 1, [], ctx, options={"salsa": "mild"})
        add_to_cart("taco_birria", 1, [], ctx, options={"salsa": "habanero"})
        return ctx

    def test_remove_specific_line_by_options(self):
        from agent.tools import remove_from_cart
        ctx = self._three_birrias()
        result = remove_from_cart("taco_birria", ctx, options={"salsa": "mild"})
        assert "error" not in result
        assert len(ctx.cart) == 2
        remaining = sorted(e["options"]["salsa"] for e in ctx.cart)
        assert remaining == ["habanero", "hot"]

    def test_remove_without_options_when_ambiguous_errors(self):
        from agent.tools import remove_from_cart
        ctx = self._three_birrias()
        result = remove_from_cart("taco_birria", ctx)
        assert "error" in result
        assert "Multiple cart lines" in result["error"]
        # Cart is unchanged.
        assert len(ctx.cart) == 3

    def test_remove_without_options_single_line_still_works(self):
        # Backward-compat: when only one line matches, options is not required.
        from agent.tools import remove_from_cart
        ctx = _CartCtx([])
        add_to_cart("taco_birria", 1, [], ctx)
        result = remove_from_cart("taco_birria", ctx)
        assert "error" not in result
        assert ctx.cart == []

    def test_remove_with_nonmatching_options_errors(self):
        from agent.tools import remove_from_cart
        ctx = self._three_birrias()
        result = remove_from_cart("taco_birria", ctx, options={"salsa": "nuclear"})
        assert "error" in result
        assert "No cart line" in result["error"]
        assert len(ctx.cart) == 3

    def test_set_quantity_targets_specific_line(self):
        from agent.tools import set_item_quantity
        ctx = self._three_birrias()
        result = set_item_quantity("taco_birria", 3, ctx, options={"salsa": "habanero"})
        assert "error" not in result
        # Find the habanero line — must be qty=3, others untouched at qty=1.
        by_salsa = {e["options"]["salsa"]: e["quantity"] for e in ctx.cart}
        assert by_salsa == {"hot": 1, "mild": 1, "habanero": 3}

    def test_set_quantity_without_options_when_ambiguous_errors(self):
        from agent.tools import set_item_quantity
        ctx = self._three_birrias()
        result = set_item_quantity("taco_birria", 2, ctx)
        assert "error" in result
        assert "Multiple cart lines" in result["error"]

    def test_update_modifiers_targets_specific_line(self):
        from agent.tools import update_item_modifiers
        ctx = self._three_birrias()
        result = update_item_modifiers(
            "taco_birria", ["add_cheese"], ctx, options={"salsa": "mild"}
        )
        assert "error" not in result
        # Only the mild line gets the cheese modifier.
        mild_line = next(e for e in ctx.cart if e["options"]["salsa"] == "mild")
        assert mild_line["modifiers"] == ["add_cheese"]
        for e in ctx.cart:
            if e["options"]["salsa"] != "mild":
                assert e["modifiers"] == []

    def test_update_modifiers_without_options_when_ambiguous_errors(self):
        from agent.tools import update_item_modifiers
        ctx = self._three_birrias()
        result = update_item_modifiers("taco_birria", ["add_cheese"], ctx)
        assert "error" in result
        assert "Multiple cart lines" in result["error"]


class TestUpdateItemModifiers:
    """update_item_modifiers replaces the modifier list on an existing cart line.
    Closes the tc_29 bug where add_to_cart with new modifiers would create a
    duplicate line instead of updating the existing item."""

    def test_add_modifier_to_existing_item(self):
        # tc_29 scenario: ordered pollo bowl (no modifiers), then asked for guac.
        ctx = _CartCtx([])
        add_to_cart("bowl_pollo", 1, [], ctx)
        assert len(ctx.cart) == 1
        assert get_cart_contents(ctx)["subtotal"] == pytest.approx(12.49, abs=0.01)

        result = update_item_modifiers("bowl_pollo", ["add_guac"], ctx)
        assert "error" not in result
        # Cart still has exactly one line — no duplicate.
        assert len(ctx.cart) == 1
        # Subtotal now includes the upcharge.
        assert get_cart_contents(ctx)["subtotal"] == pytest.approx(13.99, abs=0.01)
        assert ctx.cart[0]["modifiers"] == ["add_guac"]
        assert ctx.cart[0]["modifier_upcharge"] == pytest.approx(1.50, abs=0.01)

    def test_incremental_modifier_build(self):
        # tc_68 scenario: order then keep adding modifiers, one per turn.
        ctx = _CartCtx([])
        add_to_cart("bowl_pollo", 1, [], ctx)
        update_item_modifiers("bowl_pollo", ["add_guac"], ctx)
        update_item_modifiers("bowl_pollo", ["add_guac", "add_cheese"], ctx)
        update_item_modifiers("bowl_pollo", ["add_guac", "add_cheese", "extra_meat"], ctx)
        # Still one line.
        assert len(ctx.cart) == 1
        # $12.49 + $1.50 + $0.75 + $3.00 = $17.74
        assert get_cart_contents(ctx)["subtotal"] == pytest.approx(17.74, abs=0.01)
        assert set(ctx.cart[0]["modifiers"]) == {"add_guac", "add_cheese", "extra_meat"}

    def test_remove_modifier_via_replacement(self):
        # Customer adds a modifier then changes their mind — pass a shorter list.
        ctx = _CartCtx([])
        add_to_cart("taco_birria", 1, ["add_cheese", "no_cilantro"], ctx)
        assert get_cart_contents(ctx)["subtotal"] == pytest.approx(5.74, abs=0.01)

        update_item_modifiers("taco_birria", ["no_cilantro"], ctx)
        # Cheese is gone, no_cilantro stays.
        assert ctx.cart[0]["modifiers"] == ["no_cilantro"]
        assert get_cart_contents(ctx)["subtotal"] == pytest.approx(4.99, abs=0.01)

    def test_clear_all_modifiers_with_empty_list(self):
        ctx = _CartCtx([])
        add_to_cart("bowl_pollo", 1, ["add_guac", "add_cheese"], ctx)
        update_item_modifiers("bowl_pollo", [], ctx)
        assert ctx.cart[0]["modifiers"] == []
        assert ctx.cart[0]["modifier_upcharge"] == 0
        assert get_cart_contents(ctx)["subtotal"] == pytest.approx(12.49, abs=0.01)

    def test_quantity_preserved_through_update(self):
        # Quantity comes from add_to_cart, not from update_item_modifiers.
        ctx = _CartCtx([])
        add_to_cart("taco_birria", 3, [], ctx)
        assert ctx.cart[0]["quantity"] == 3
        update_item_modifiers("taco_birria", ["add_cheese"], ctx)
        assert ctx.cart[0]["quantity"] == 3
        # 3 × ($4.99 + $0.75) = $17.22
        assert get_cart_contents(ctx)["subtotal"] == pytest.approx(17.22, abs=0.01)

    def test_update_item_not_in_cart_returns_error(self):
        ctx = _CartCtx([])
        result = update_item_modifiers("taco_birria", ["add_cheese"], ctx)
        assert "error" in result
        assert "not in the cart" in result["error"].lower()

    def test_update_unknown_item_id_returns_error(self):
        # Item ID doesn't exist on the menu at all.
        ctx = _CartCtx([{
            "item_id": "ghost_item", "name": "Ghost", "price": 1.00,
            "quantity": 1, "modifiers": [], "modifier_upcharge": 0.0, "line_total": 1.00,
        }])
        result = update_item_modifiers("ghost_item", ["add_guac"], ctx)
        assert "error" in result

    def test_no_duplicate_line_after_update(self):
        # The whole point: never grow the cart line count from an update.
        ctx = _CartCtx([])
        add_to_cart("bowl_pollo", 1, [], ctx)
        for mods in (["add_guac"], ["add_cheese"], ["extra_meat"], []):
            update_item_modifiers("bowl_pollo", mods, ctx)
        assert len(ctx.cart) == 1


class TestSetItemQuantity:
    """set_item_quantity REPLACES the quantity on an existing cart line.
    Closes the tc_28/tc_39/tc_67 bug where 'make it 2' was interpreted as
    'add 2 more' via add_to_cart, ending with quantity=3 instead of 2."""

    def test_set_qty_replaces_not_increments(self):
        # tc_67 scenario: cart has qty=1, customer says "make it 2".
        ctx = _CartCtx([])
        add_to_cart("taco_birria", 1, [], ctx)
        result = set_item_quantity("taco_birria", 2, ctx)
        assert "error" not in result
        assert ctx.cart[0]["quantity"] == 2
        # 2 × $4.99 = $9.98
        assert get_cart_contents(ctx)["subtotal"] == pytest.approx(9.98, abs=0.01)

    def test_set_lower_quantity(self):
        # tc_39 scenario: cart has qty=2, customer says "make it 1".
        ctx = _CartCtx([])
        add_to_cart("taco_birria", 2, [], ctx)
        result = set_item_quantity("taco_birria", 1, ctx)
        assert "error" not in result
        assert ctx.cart[0]["quantity"] == 1
        assert get_cart_contents(ctx)["subtotal"] == pytest.approx(4.99, abs=0.01)

    def test_set_qty_preserves_modifiers_and_upcharge(self):
        # Setting quantity must not touch the modifier list or upcharge.
        ctx = _CartCtx([])
        add_to_cart("bowl_pollo", 1, ["add_guac", "add_cheese"], ctx)
        set_item_quantity("bowl_pollo", 3, ctx)
        assert ctx.cart[0]["modifiers"] == ["add_guac", "add_cheese"]
        # 3 × ($12.49 + $1.50 + $0.75) = 3 × $14.74 = $44.22
        assert get_cart_contents(ctx)["subtotal"] == pytest.approx(44.22, abs=0.01)

    def test_set_qty_to_same_value_is_noop(self):
        ctx = _CartCtx([])
        add_to_cart("taco_birria", 2, [], ctx)
        set_item_quantity("taco_birria", 2, ctx)
        assert ctx.cart[0]["quantity"] == 2
        assert get_cart_contents(ctx)["subtotal"] == pytest.approx(9.98, abs=0.01)

    def test_set_qty_zero_returns_error(self):
        # Zero should never silently remove the line — that's what remove_from_cart is for.
        ctx = _CartCtx([])
        add_to_cart("taco_birria", 1, [], ctx)
        result = set_item_quantity("taco_birria", 0, ctx)
        assert "error" in result
        # Cart unchanged.
        assert ctx.cart[0]["quantity"] == 1

    def test_set_negative_returns_error(self):
        ctx = _CartCtx([])
        add_to_cart("taco_birria", 1, [], ctx)
        result = set_item_quantity("taco_birria", -1, ctx)
        assert "error" in result
        assert ctx.cart[0]["quantity"] == 1

    def test_set_qty_item_not_in_cart_returns_error(self):
        ctx = _CartCtx([])
        result = set_item_quantity("taco_birria", 2, ctx)
        assert "error" in result
        assert "not in the cart" in result["error"].lower()

    def test_no_duplicate_line_after_set(self):
        ctx = _CartCtx([])
        add_to_cart("taco_birria", 1, [], ctx)
        for n in (2, 5, 3, 1):
            set_item_quantity("taco_birria", n, ctx)
        assert len(ctx.cart) == 1
        assert ctx.cart[0]["quantity"] == 1
