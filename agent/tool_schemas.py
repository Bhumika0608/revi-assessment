"""Claude tool definitions for the ordering agent."""

TOOL_SCHEMAS = [
    {
        "name": "search_menu",
        "description": (
            "Search the menu using hybrid semantic + keyword search. "
            "Understands natural language including vague mood queries ('something comforting', "
            "'something light'), price constraints ('under $5', 'less than $10'), "
            "and dietary filters ('vegan', 'gluten-free'). "
            "Returns a structured result with a 'match' field: "
            "'exact' — one clear item identified, use top_item['id'] directly. "
            "'ambiguous' — multiple valid items found, ask ONE clarifying question using 'items'. "
            "'none' — nothing matches. "
            "Always call this before claiming any item is or isn't on the menu."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "What to search for. Can be a specific item ('birria taco'), "
                        "a vague description ('something warm and hearty'), "
                        "a price constraint ('tacos under $6'), "
                        "or a dietary need ('vegan bowl'). "
                        "Pass the customer's words as-is — the search engine handles interpretation."
                    ),
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_faq",
        "description": (
            "Answer questions about the restaurant itself — parking, WiFi, hours, delivery, "
            "allergen info, dietary options, reservations, restrooms, kids menu, payment, etc. "
            "For dietary questions ('what vegan options do you have?') this also queries the live menu. "
            "Call this instead of making up restaurant information."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The customer's question about the restaurant.",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_item_details",
        "description": (
            "Get the full details for a specific menu item: price, description, options (with required flag), "
            "modifiers (with prices), dietary tags, and availability. "
            "Always call this before adding an item to the cart with add_to_cart."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "item_id": {
                    "type": "string",
                    "description": "The item's unique ID as returned by search_menu (e.g. 'taco_birria').",
                }
            },
            "required": ["item_id"],
        },
    },
    {
        "name": "add_to_cart",
        "description": (
            "Add a validated menu item to the customer's cart. "
            "MUST call get_item_details first — the item_id must be validated before adding. "
            "Price is always fetched from the database — never calculate it yourself. "
            "Call this immediately after get_item_details in the SAME tool-use iteration. "
            "For multi-item orders: get_item_details all items, then add_to_cart all items. "
            "Calling add_to_cart again for an item already in the cart with the SAME modifiers "
            "AND SAME options INCREASES its quantity; different options (e.g. different salsa "
            "choice) create a SEPARATE cart line. So if the customer asks for three tacos with "
            "different salsas, issue three separate add_to_cart calls — one per salsa choice — "
            "and each becomes its own line."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "item_id": {
                    "type": "string",
                    "description": "Validated item ID from get_item_details.",
                },
                "quantity": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "How many of this item to add. Default 1 if not specified.",
                },
                "modifiers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Modifier IDs explicitly requested by customer (e.g. ['add_guac', 'no_cilantro']).",
                },
                "options": {
                    "type": "object",
                    "description": (
                        "Choice-style options like {\"salsa\": \"hot\", \"tortilla\": \"corn\"}. "
                        "Use only keys/values listed in the item's `options` field from "
                        "get_item_details. Lines with different option values stay SEPARATE "
                        "in the cart — to give two tacos different salsas, make two add_to_cart "
                        "calls with different options dicts. Omit (or pass {}) if the customer "
                        "didn't specify a choice."
                    ),
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["item_id", "quantity"],
        },
    },
    {
        "name": "remove_from_cart",
        "description": (
            "Remove a cart line by item_id. When multiple lines exist for the same "
            "item_id (per-line options — e.g. three birria tacos with different salsas), "
            "pass `options` to pick which one. Without `options` in that case the call "
            "errors and lists the lines so you can re-issue with the right key."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "item_id": {
                    "type": "string",
                    "description": "The item_id to remove.",
                },
                "options": {
                    "type": "object",
                    "description": (
                        "Options dict matching the line to remove "
                        "(e.g. {\"salsa\": \"hot\"}). Only needed when multiple cart lines "
                        "share the same item_id."
                    ),
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["item_id"],
        },
    },
    {
        "name": "set_item_quantity",
        "description": (
            "Set the quantity on an item ALREADY in the cart (REPLACE, not increment). Use "
            "this — not add_to_cart — when the customer says 'make it 2', 'actually 3', "
            "'change to N', 'I want N total', or any other SET-the-quantity intent. Calling "
            "add_to_cart(quantity=N) would ADD N more on top of the existing quantity, which "
            "is the opposite of what these phrasings mean.\n\n"
            "To increment instead (customer says 'add another', 'one more'), use add_to_cart "
            "— it keeps the additive semantics. To remove the item entirely, use "
            "remove_from_cart.\n\n"
            "When multiple cart lines share the same item_id (per-line options), pass "
            "`options` to pick which line; otherwise the call errors and lists the lines."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "item_id": {
                    "type": "string",
                    "description": "The item_id of the existing cart line to update.",
                },
                "quantity": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "The NEW total quantity. Must be >= 1.",
                },
                "options": {
                    "type": "object",
                    "description": (
                        "Options dict matching the line to update "
                        "(e.g. {\"salsa\": \"hot\"}). Only needed when multiple cart lines "
                        "share the same item_id."
                    ),
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["item_id", "quantity"],
        },
    },
    {
        "name": "update_item_modifiers",
        "description": (
            "Replace the modifier list on an item ALREADY in the cart. Use this — not "
            "add_to_cart — when the customer wants to add, change, or remove modifiers on "
            "something already ordered ('add guac to my bowl', 'no cilantro on the birria taco', "
            "'change the salsa to hot'). Calling add_to_cart with new modifiers would create a "
            "duplicate line.\n\n"
            "The `modifiers` argument must be the FULL new modifier list — include any existing "
            "modifiers you want to keep, omit ones being removed.\n\n"
            "When multiple cart lines share the same item_id (per-line options), pass "
            "`options` to pick which line; otherwise the call errors and lists the lines."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "item_id": {
                    "type": "string",
                    "description": "The item_id of the existing cart line to update.",
                },
                "modifiers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Full new modifier list (replaces what's there). "
                        "E.g. ['add_guac', 'add_cheese']. Empty list to clear all modifiers."
                    ),
                },
                "options": {
                    "type": "object",
                    "description": (
                        "Options dict matching the line to update "
                        "(e.g. {\"salsa\": \"hot\"}). Only needed when multiple cart lines "
                        "share the same item_id."
                    ),
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["item_id", "modifiers"],
        },
    },
    {
        "name": "get_cart",
        "description": "Return the current cart contents, item count, and subtotal. Use when customer asks what's in their cart.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "signal_checkout",
        "description": (
            "Signal that the customer is done adding items and ready to proceed to payment. "
            "Call this when the customer says 'that's all', 'checkout', 'confirm', 'place my order', "
            "'yes go ahead', or any clear indication they are finished ordering. "
            "Do NOT call this if the cart is empty. "
            "After calling this, say something short like 'Perfect, heading to checkout!' "
            "Do NOT mention prices, taxes, or fees — the checkout system handles all of that."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]
