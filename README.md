# Talkin' Tacos — Ordering Agent

A conversational ordering agent for Talkin' Tacos, a Mexican restaurant in Miami's Wynwood neighborhood. Customers order in natural language — the agent clarifies when needed, resolves typos and ambiguity, builds a real-time cart, then hands off to a deterministic checkout flow with Stripe payment.

Built with the raw Anthropic SDK, SQLite + FTS5 + semantic embeddings (fastembed), and a custom eval harness. No frameworks.

**Scope:** 10,089-item catalog (29 canonical + 10,060 generated at scale), real-time inventory tracking, pickup/delivery zones with fee calculation, Miami-Dade tax, and a Streamlit UI handling the full order-to-payment flow.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                               CUSTOMER                                    │
│                       (natural-language orders)                           │
└─────────────────────────────────┬─────────────────────────────────────────┘
                                  │
┌─────────────────────────────────▼─────────────────────────────────────────┐
│                       PRESENTATION LAYER · Streamlit                       │
│                                                                            │
│   ┌──────────────────────────────┐      ┌──────────────────────────────┐  │
│   │  ui/app.py                   │      │  ui/pages/1_Inventory.py     │  │
│   │  • Chat UI                   │      │  • Admin page (gated)        │  │
│   │  • 4-step checkout flow      │      │  • restock · browse · log    │  │
│   │  • per-turn trace expander   │      │                              │  │
│   └──────────────────────────────┘      └──────────────────────────────┘  │
└────────────────┬───────────────────────────────────────┬───────────────────┘
                 │ take_order()                           │ deterministic
                 │ (cart-building only)                   │ (NO LLM)
                 ▼                                        ▼
┌──────────────────────────────────┐    ┌──────────────────────────────────┐
│      AGENT LAYER · agent/        │    │   CHECKOUT LAYER · db/ (Python)  │
│                                  │    │                                  │
│  ┌────────────────────────────┐  │    │  Step 1  fulfillment             │
│  │ Pre-LLM short-circuits     │  │    │  Step 2  delivery.py → zone+fee  │
│  │ • checkout signals         │  │    │  Step 3  tax.py → 8% tax         │
│  │ • FAQ queries              │  │    │  Step 4  payment.py → Stripe     │
│  └────────────────────────────┘  │    │                                  │
│  ┌────────────────────────────┐  │    │  finalize.py (after success):    │
│  │ ReAct loop (max 8 iters)   │  │    │  • save_order()                  │
│  │ search → details → add     │  │    │  • decrement_inventory()         │
│  │ → signal_checkout          │  │    │  • send_receipt() (email.py)     │
│  └────────────────────────────┘  │    │  idempotent via order_id         │
│  ┌────────────────────────────┐  │    └────────────────┬─────────────────┘
│  │ Python enforcement         │  │                     │
│  │ • validated_ids gate       │  │                     │
│  │ • status from tool trace   │  │                     │
│  │ • prices from DB           │  │                     │
│  └────────────────────────────┘  │                     │
│  prompts · tool_schemas · trace  │                     │
└────────┬─────────────────┬───────┘                     │
         │ 9 tools         │ LLM calls                    │
         ▼                 │                              ▼
┌──────────────────────────┼──────────────────────────────────────────────┐
│                  DATA & SEARCH LAYER · db/ + SQLite                       │
│                                                                          │
│   ┌────────────────────────────┐      ┌────────────────────────────────┐ │
│   │ db/search.py               │      │ data/menu.db (rebuilt from JSON)│ │
│   │ • Semantic (fastembed/BGE) │      │ • menu_items (10,089 · FTS5)   │ │
│   │ • FTS5 BM25          ─RRF─▶ │─────▶│ • inventory + inventory_log    │ │
│   │ • rapidfuzz (typos)        │      │ • orders · payments            │ │
│   └────────────────────────────┘      └────────────────────────────────┘ │
│   in-memory caches: 10k items · embedding matrix                         │
└────────────────────────────────┬─────────────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                           EXTERNAL SERVICES                                │
│                                                                            │
│   ┌─────────────────────┐  ┌─────────────────────┐  ┌──────────────────┐  │
│   │ Anthropic API       │  │ Stripe (test mode)  │  │ Gmail SMTP       │  │
│   │ • ReAct LLM calls   │  │ • PaymentIntent     │  │ • order receipts │  │
│   │ • prompt-cached ~92%│  │ • card→pm_card_*    │  │ • optional       │  │
│   └─────────────────────┘  └─────────────────────┘  └──────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
```

**The core boundary:** the LLM has exactly one job — build the cart. Checkout, payment, tax, and order persistence run in deterministic Python with zero LLM calls. The agent never sees card data and never saves an order.

---

## Quick Start

```bash
# 1. Clone / unzip
cd talkin-tacos

# 2. One-command setup (venv + Python deps + DB Browser for SQLite on macOS)
bash setup.sh
source venv/bin/activate

# 3. Add your API keys
cp .env.example .env
# Edit .env:
#   ANTHROPIC_API_KEY=sk-ant-...
#   STRIPE_SECRET_KEY=sk_test_...   (test mode — card data mapped to Stripe test tokens)
#   ADMIN_PASSWORD=...               (gates the Inventory admin page — see Design Decision §15)

# 4. Run the web UI
streamlit run ui/app.py

# 5. CLI walkthrough
python3 demo.py

# 6. Full eval suite (88 cases — requires ANTHROPIC_API_KEY)
python3 -m evals.run_evals

# 7. Unit tests (395 tests, no API key needed)
python3 -m pytest tests/ -v
```

Switch models via `.env` (`CLAUDE_MODEL=claude-haiku-4-5-20251001`) or inline:
```bash
CLAUDE_MODEL=claude-haiku-4-5-20251001 python3 -m evals.run_evals
```

**Inspecting the database:** `setup.sh` installs [DB Browser for SQLite](https://sqlitebrowser.org) (macOS, via Homebrew). Open it with:
```bash
open -a "DB Browser for SQLite" data/menu.db
```
Click **Browse Data** and select a table — `menu_items` for the full 10k catalog, `orders` for confirmed orders, `payments` for Stripe transactions, `inventory` for live stock levels, `inventory_log` for every stock change. To query from the terminal without the GUI:
```bash
# All confirmed orders with totals
sqlite3 data/menu.db ".headers on" ".mode column" \
  "SELECT order_id, subtotal, tax, total, fulfillment_type, created_at FROM orders;"

# Live inventory for canonical items
sqlite3 data/menu.db ".headers on" ".mode column" \
  "SELECT m.name, i.quantity, i.low_stock_threshold FROM inventory i JOIN menu_items m ON m.id = i.item_id ORDER BY i.quantity;"

# Stripe payment records
sqlite3 data/menu.db ".headers on" ".mode column" \
  "SELECT order_id, stripe_payment_id, amount_cents, status FROM payments ORDER BY id DESC LIMIT 20;"
```

> **Platform note:** `setup.sh` installs DB Browser for SQLite via Homebrew (macOS only). On Linux/Windows, install pip deps manually (`python3 -m venv venv && pip install -r requirements.txt`) and skip the DB Browser step — the `sqlite3` CLI does the same job.

---

## Configuration

All runtime configuration is read from environment variables (with `.env` auto-loaded by `python-dotenv` on first miss). Required vs. optional is enforced where it matters; missing optional vars degrade specific features gracefully without crashing the app.

| Env var | Required for | Default | Notes |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | The agent (`take_order`), evals | — | Without this, the chat UI cannot make LLM calls. Unit tests don't need it. |
| `STRIPE_SECRET_KEY` | Checkout payment | — | Use `sk_test_...` for test mode. `process_payment` errors loudly without it. |
| `CLAUDE_MODEL` | Model selection | `claude-sonnet-4-6` | Set to `claude-haiku-4-5-20251001` for cheaper/faster runs. |
| `ADMIN_PASSWORD` | Inventory admin page | — | Unset → page is hard-locked. Required to enable restock UI and order history. See Design Decision §15. |
| `GMAIL_SENDER` + `GMAIL_APP_PASSWORD` | Order receipt emails | — | Both required together. If either is missing, the receipt step is skipped with a logged warning; the order itself still confirms. |
| `LLM_REQUEST_TIMEOUT_S` | Agent resilience | `30.0` | Per-request Anthropic timeout in seconds. SDK default is 600s; we override to keep the UI responsive. |
| `LLM_MAX_RETRIES` | Agent resilience | `2` | SDK's internal retry budget for transient errors (rate limit, 5xx, connection). |
| `LOG_LEVEL` | Logging | `INFO` | Standard Python logging levels. Set to `DEBUG` to surface tool dispatch traces. |
| `MENU_DB_PATH` | DB location | `data/menu.db` | Override for testing or non-default deployment layouts. |

`.env.example` is the canonical template. Copy and edit:

```bash
cp .env.example .env
```

---

## How the Agent Works

The agent runs a **ReAct loop** — on each iteration it makes one LLM call, executes any tool the LLM requests, appends the result to the conversation, and loops. It stops when the LLM returns `end_turn` (done talking or needs to ask a question) or when `signal_checkout` is called.

The agent's only job is **building the cart**. Checkout, payment, and order persistence are all handled by deterministic Python code after the agent is done — the agent never sees card data and never saves an order.

```
  User message + conversation history + current cart + validated_ids
              │
              ▼
  ┌─────────────────────────────────────────────────────────┐
  │  system prompt  (prompt-cached)                         │
  │  conversation history  (all prior turns + tool results) │
  │  user message                                           │
  └─────────────────────────────────────────────────────────┘
              │
              ▼
  ┌─── ReAct loop (max 8 iterations) ──────────────────────────────────────────┐
  │                                                                             │
  │   Claude LLM call                                                           │
  │        │                                                                    │
  │        ├── stop_reason: "tool_use" ──► dispatch tool                       │
  │        │                                  │                                 │
  │        │   search_menu(query)             │  → {match, items, top_item}    │
  │        │   search_faq(query)              │  → {found, answer}             │
  │        │   get_item_details(item_id)      │  → price, options, modifiers   │
  │        │   add_to_cart(item_id, qty, ...) │  → {added, quantity, subtotal} │
  │        │   update_item_modifiers(id, [..])│  → {updated, line_total, ...}  │
  │        │   set_item_quantity(id, qty)     │  → {updated, quantity, ...}    │
  │        │   remove_from_cart(item_id)      │  → {removed}                   │
  │        │   get_cart()                     │  → {items, count, subtotal}    │
  │        │   signal_checkout()              │  → {ready: true}               │
  │        │                                  │                                 │
  │        │         append tool result to messages ──► loop again              │
  │        │                                                                    │
  │        └── stop_reason: "end_turn" ─────────────────────────────────────►  │
  │                                                                        exit │
  └────────────────────────────────────────────────────────────────────────────┘
              │
              ▼
  ┌──────────────────────────────────────────────┐
  │  Derive status from tool call trace          │
  │    signal_checkout called?  →  checkout      │
  │    REFUSED: prefix?         →  refused       │
  │    otherwise                →  in_progress   │
  └──────────────────────────────────────────────┘
              │
              ▼ (on "checkout")
  ┌── 4-step deterministic checkout (UI) ────────────────────────────────────┐
  │                                                                           │
  │  Step 1: Fulfillment — Pickup or Delivery?                               │
  │  Step 2: Delivery address + zone lookup (if delivery)                    │
  │  Step 3: Order review — subtotal + delivery fee + 8% tax = total         │
  │  Step 4: Payment — Stripe form (test mode: card → pm_card_* token)       │
  │                                                                           │
  │  On payment success:                                                      │
  │    save_order()          — write order to DB with full breakdown          │
  │    decrement_inventory() — reduce stock for each ordered item             │
  │    show confirmation     — order ID, ETA, transaction ID                  │
  └───────────────────────────────────────────────────────────────────────────┘
  { agent_message, status, cart, validated_ids, trace }
```

---

**Concrete example — "Birria taco, no cilantro please"**

| Iteration | Tool called | What happens |
|-----------|-------------|-------------|
| 1 | `search_menu("birria taco no cilantro")` | Hybrid search (semantic + FTS5 + fuzzy) returns `{match: "exact", top_item: {id: "taco_birria", price: 4.99}}` |
| 2 | `get_item_details("taco_birria")` | DB returns full item: price, available modifiers including `no_cilantro`. Python adds `"taco_birria"` to `validated_ids`. |
| 3 | `add_to_cart("taco_birria", 1, ["no_cilantro"])` | Cart updated with price from DB. Returns `{added: "Birria Taco", quantity: 1, subtotal: 4.99}`. |
| — | `end_turn` | LLM says: *"One birria taco, no cilantro — added! Anything else?"* |

When customer says "that's all" → `signal_checkout()` → status becomes `"checkout"` → 4-step UI takes over.

---

**Five principles that hold across every interaction:**

**1. Cart state is explicit Python, not the conversation history.** A `_CartCtx` object is passed into every `take_order()` call and returned with the result. Cart contents are always the authoritative list — the LLM cannot invent or modify the cart by talking about it. Modifier upcharges (`add_guac` $1.50, `extra_meat` $3.00, etc.) are looked up from the menu and folded into `line_total`; the LLM picks modifier IDs but never sees dollars.

**2. Tool chain enforced at the Python layer.** `_run_loop` tracks `validated_ids` — item IDs confirmed via `get_item_details` in the current session, persisted across turns. If `add_to_cart` receives an ID not in that set, the dispatch layer returns an error before executing, forcing the LLM to self-correct. Prompt instructions tell the LLM what to do; the Python guard ensures correctness if it doesn't.

**3. Status comes from the tool trace, not LLM text.** The LLM could say "heading to checkout!" before ever calling `signal_checkout`. That text is ignored. Status is derived deterministically: `signal_checkout` called → `checkout`, `REFUSED:` prefix → `refused`, everything else → `in_progress`.

**4. Payment and order persistence are idempotent and happen only after payment succeeds.** The agent never places an order. `save_order` and `decrement_inventory` are called by the UI after Stripe returns `status: "succeeded"`. A stable `order_id` reserved at the payment step doubles as Stripe's `idempotency_key`; `decrement_inventory` dedupes via `inventory_log`; `_finalize_order` short-circuits when `order_exists(order_id)`. A Streamlit rerun during the payment spinner cannot double-charge or double-decrement. An abandoned checkout leaves no phantom order.

**5. The LLM call is bounded and recoverable.** The Anthropic client uses a 30-second request timeout and a bounded retry budget for transient errors. When retries are exhausted, the four terminal exception classes (`APITimeoutError`, `RateLimitError`, `APIConnectionError`, generic `APIError`) each return a short customer-facing fallback and are recorded in the trace with the matching `stop_reason`. The cart and `validated_ids` survive the failed turn so the customer can retry without rebuilding their order.

---

## Design Decisions & Tradeoffs

### 1. Raw Anthropic SDK over LangChain
LangChain abstracts the tool loop — you lose visibility into iteration count, tool call ordering, and error propagation. For a 9-tool agent where loop behavior directly affects cost and correctness, ~150 lines of raw SDK is better than a framework. LangChain becomes worth it at 20+ tools or when you need pre-built memory stores.

### 2. Why ReAct over a Single LLM Call (and over Plan-and-Execute)
A single LLM call can't search the menu, look up item details, and build the cart in one shot — it would have to hallucinate prices and IDs from training data. The ReAct loop lets the model gather real data (search → lookup) before acting (add to cart). Each tool result is appended to the message history, so the model always reasons over real DB output, not its own prior text.

A plan-and-execute pattern would also work, but offers no real win at this product's per-turn shape (1–3 tool calls, next step depends on the last result, ambiguity needs to be resolved mid-loop). See §If Shipping to Production — *Agent Pattern at Scale* for the analysis of where ReAct stops being the right call and how we'd layer in planning then.

### 3. System Prompt Design
**Approach: system prompt only — no few-shot examples, no chain-of-thought scaffolding.**

The system prompt (`agent/prompts.py`) does one job: ground every response in real menu data rather than model memory. The core rule is a mandatory tool chain — the agent must call `search_menu` before claiming anything about the menu, then `get_item_details` before adding to cart. The prompt instructs the LLM on this sequence; the Python layer enforces it (see §7).

### 4. Explicit Cart Object
The cart is a `list[dict]` owned by the caller, not the LLM. `take_order()` receives `current_cart`, passes it through the `_CartCtx` to all cart tools, and returns the updated `cart`. This means:
- Cart state can never diverge from what the LLM thinks was added — the tools are the authoritative source
- Price is always fetched from the DB in `add_to_cart` — the LLM never calculates totals
- Partial cancellations (`remove_from_cart`) operate on a real list, not the LLM's reconstructed memory of the order
- `signal_checkout` fails if the cart is empty — a structural guard, not a prompt instruction

The tradeoff: token cost grows with conversation length (same as before). The explicit cart doesn't help with that — but it eliminates the harder problem of cart/LLM state divergence.

### 5. Menu DB: Why SQLite Instead of Reading JSON Directly
`menu.json` is the canonical source (29 items) and `menu_expanded.json` holds the full 10,060-item generated catalog. Neither is read at runtime. On startup, `init_db()` loads both into a SQLite database with an FTS5 virtual table. There are four reasons for this:

1. **Full-text search.** A JSON file has no query capability. SQLite FTS5 gives BM25-ranked search across item name, description, category, and tags in a single call — no loading everything into memory and filtering in Python.
2. **Inventory as source of truth.** Availability is derived live from the `inventory` table. `get_item_details` JOINs `inventory` on every call, so `available` reflects current stock. An item with `quantity = 0` is reported as unavailable immediately; no menu edit required.
3. **Order persistence.** Confirmed orders (with full breakdown: subtotal, delivery fee, tax, total) are written to an `orders` table. Stripe payment records go to a `payments` table. Keeping menu, inventory, orders, and payments in one file keeps the data layer simple.
4. **Scale.** At 10k items, FTS5 + in-memory rapidfuzz cache handles search without degradation. A plain JSON scan would load and iterate 10k dicts on every query.

### 6. Hybrid Search: Semantic + FTS5 + Fuzzy
`db/search.py` implements three-tier hybrid search merged via Reciprocal Rank Fusion (RRF):

1. **Semantic (fastembed / BAAI/bge-small-en-v1.5)** — dense vector embeddings, cosine similarity. Handles natural-language queries like "something comforting and warm" → consomé, "light lunch" → salad bowls. Model runs locally, no external API.
2. **FTS5 BM25** — full-text across name, description, category, tags with Porter stemmer. Fast exact and stem matches.
3. **Rapidfuzz fuzzy** — character-level similarity for typo recovery. "birria tcao" → "Birria Taco".

RRF merges all three ranked lists by position, not score, so no per-source weight tuning is needed. Python-side disambiguation then applies, in this order:

1. **Clear-winner fast-path** — three rules, any one fires `match: "exact"`. Score is `max(token_sort_ratio, partial_ratio)` so informal abbreviations match partially ("chips and guac" → "Chips & Guacamole"):
   - **A:** Strong score advantage — top match `≥ 85`, gap to runner-up `≥ 15`.
   - **B:** Same score, shorter canonical — top `≥ 85`, gap `< 5`, runner-up name `≥ 5` chars longer (so canonical "Birria Taco" beats the longer "Birria Taco Salad" when both score 100 via partial match).
   - **C:** Canonical beats synthetic at near-equal score — top is in the canonical 29-item menu, runner-up is a synthetic variant, top `≥ 80`, gap `≥ -2` (so "carne asada taco" picks the canonical over "Carne Asada Norteño Taco" when both score the same).
2. **Food-type filter** — narrow candidates to the right format (taco / burrito / bowl / …) based on the query's food noun.
3. **Descriptor match** — if exactly one remaining candidate has a unique descriptor word from the query (e.g. "cheese" in "cheese quesadilla"), return that as exact.
4. **Ambiguous fallback** — return up to 5 candidates with `match: "ambiguous"` so the LLM asks one clarifying question.

The `match` flag (`"exact"` / `"ambiguous"` / `"none"`) tells the LLM what to do; it never applies sorting heuristics itself.

**FAQ search** (`search_faq`) runs the same semantic model over `data/faq.json` (restaurant info: hours, parking, WiFi, allergies, reservations, etc.). Dietary queries ("do you have vegan options?") additionally query the live menu for real-time item counts.

**Dietary filtering — positive and negative tags.** `parse_dietary_filter` maps natural-language phrases ("dairy free", "lactose intolerant", "non-dairy", "gluten free", "celiac friendly", "plant-based", etc.) onto canonical dietary tags. `items_matching_dietary` then applies them in two modes:

- **Positive tags** (`vegan`, `vegetarian`, `chicken`, `beef`, …) — return items where the tag appears in `dietary_tags`.
- **Negative "X-free" tags** (`dairy-free`, `gluten-free`, hypothetically `nut-free` / `soy-free` once the menu adds them) — return items where `contains_X` is **absent** from `dietary_tags`. The menu uses `contains_dairy` / `contains_gluten` *inclusion* markers rather than `dairy-free` *exclusion* markers, so the filter has to invert. This generalizes — the moment the menu starts tagging `contains_nut`, "nut-free" works without code changes once the synonym is in `_DIETARY_MAP`.

### 7. Tool Chain Enforcement — Python State Machine
Prompt instructions are not architectural guarantees. A well-tuned model follows them reliably, but it can still skip `get_item_details` and pass a guessed ID to `add_to_cart` — a wrong ID either fails silently or adds a phantom item.

`_run_loop` enforces the chain at the Python layer using a `validated_ids` set that **persists across conversation turns**:
- Every successful `get_item_details(item_id)` adds `item_id` to the set.
- When `add_to_cart` is dispatched, `_dispatch_tool` checks `item_id` against `validated_ids`. Any unvalidated ID returns an error to the LLM: `"Cannot add 'X' to cart. Call get_item_details('X') first to validate it."` The LLM self-corrects on the next iteration.
- `validated_ids` is passed into `take_order()` and returned in the result, so re-validating already-seen items across turns is avoided.

The prompt still instructs the correct sequence; the Python guard turns a failure to follow instructions into a recoverable self-correction instead of a silent bad order.

### 8. Deterministic Checkout (No LLM in Payment)
When `signal_checkout` fires, the UI enters a 4-step flow that runs entirely in Python with no LLM calls:

- **Step 1** — Pickup or delivery selection
- **Step 2** — Delivery address → ZIP/neighborhood → zone lookup → delivery fee ($2.99 / $4.99 / $7.99). Minimum order for delivery: $10. Coverage: Miami-Dade ZIP codes within ~10 miles of Wynwood.
- **Step 3** — Full order review: subtotal + delivery fee + 8% Miami-Dade tax (7% FL state + 1% county surtax) = total. Customer sees exact amount before paying.
- **Step 4** — Stripe payment form. In test mode, the entered card number is mapped to a Stripe pre-built test payment method token (`pm_card_visa`, `pm_card_mastercard`, etc.) — raw card digits are never sent to the Stripe API. Only the PaymentIntent ID, amount, and status are stored.

### 9. Order Only Saved After Payment
`save_order()` and `decrement_inventory()` are called from `_finalize_order()` in the UI — only after `process_payment()` returns `success: True`. An abandoned checkout (user closes tab at Step 3, card declined) leaves no order in the database and no inventory decremented.

### 10. Tax Calculation
`db/tax.py` applies Miami-Dade County's prepared food tax rate: 8% (7% Florida state + 1% county discretionary surtax). Tax is computed deterministically in Python at checkout — the LLM never sees or calculates dollar amounts.

### 11. Full-Scale Menu (10,000+ Items)
`scripts/generate_menu.py` generates `menu_expanded.json` — 10,060 items across 24 categories. These are treated as the real catalog, not stress-test fixtures. The generator uses a dimensional approach (proteins × styles × sizes):

| Category    | Formula                              | Count |
|-------------|--------------------------------------|------:|
| Tacos       | 53 styles × 30 proteins × 3 sizes    | 4,770 |
| Burritos    | 20 styles × 30 proteins × 3 sizes    | 1,800 |
| Bowls       | 12 styles × 30 proteins × 3 sizes    | 1,080 |
| Quesadillas | 10 styles × 30 proteins              |   300 |
| Nachos      | 8 styles × 30 proteins               |   240 |
| Enchiladas  | 8 sauces × 30 proteins               |   240 |
| + 18 more categories (tortas, drinks, desserts, kids, combos, …) | | ~1,630 |
| **Total**   |                                      | **10,060** |

The 30 proteins span beef, pork, chicken, seafood, vegetarian, and premium (lobster, duck confit, smoked brisket). All items include realistic prices, modifiers, dietary tags, and FTS-indexed descriptions. The full 10,089-item DB (10,060 generated + 29 originals) is what the agent and evals run against.

**Search performance at scale:** `get_all_items()` is called once per process and cached in memory (`_all_items_cache`). FTS5 BM25 queries run against the SQLite index — no full table scans. Semantic embeddings are pre-computed at init and cached in memory. Measured search latency is unchanged from the 500-item baseline.

### 12. No Redundant Tool Calls Across Turns
`validated_ids` is persisted in `st.session_state` across turns. If a customer says "actually, add another birria taco" on turn 3, the agent can call `add_to_cart("taco_birria", 1)` directly — `taco_birria` is already validated, no `get_item_details` round-trip needed. This eliminates the main source of extra tool calls in multi-turn conversations.

### 13. Prompt Caching
The system prompt is sent with `cache_control: ephemeral`. Anthropic caches it for 5 minutes, serving subsequent requests at ~10% of normal input token cost. On a 5-turn conversation, this saves ~80% of input tokens on turns 2–5.

In production, **multi-breakpoint caching** extends this to conversation history: mark the last assistant message with `cache_control` so the full history prefix is cached on every subsequent turn. Anthropic supports up to 4 cache markers per request.

### 14. Inventory System
Every confirmed order (after payment) decrements stock in real time. The inventory layer has four components:

**Schema (two tables in `data/menu.db`):**
```sql
inventory (
    item_id             TEXT PRIMARY KEY,
    quantity            INTEGER NOT NULL DEFAULT 0,
    low_stock_threshold INTEGER NOT NULL DEFAULT 10,
    updated_at          TEXT
)

inventory_log (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id   TEXT,
    delta     INTEGER,        -- negative for orders, positive for restocks
    reason    TEXT,           -- 'order' | 'restock'
    order_id  TEXT,
    logged_at TEXT
)
```

**Seeding:** On first `init_db()`, every item gets `quantity = 100` (or `0` if marked unavailable in `menu.json`). Seeding uses `INSERT OR IGNORE` so live stock is never overwritten on restart.

**Order flow:** `decrement_inventory(item_id, quantity, order_id)` is called from `_finalize_order()` in the UI after payment succeeds — not by the agent. The decrement is atomic (`MAX(0, quantity - ?)`) and always writes to `inventory_log`.

**Availability:** `get_item_by_id` JOINs `inventory` on every call. `available` is derived from `quantity > 0` — the static JSON flag is overridden by live stock. If stock hits 0 mid-session, the next `get_item_details` call returns `available: false` and the agent informs the customer.

**Admin UI (`ui/pages/1_Inventory.py`):** A second Streamlit page accessible from the sidebar, gated behind `ADMIN_PASSWORD` (see §15). Designed restock-first — operators come here to *do something*, not to browse 10k items:

- **Header** — KPI tiles (total SKUs, in stock, low stock, out of stock); icon-only refresh and lock controls.
- **Three tabs**, restock promoted to the first position:
  - **🔄 Restock** — primary action. Two-column split: form on the left (optional category filter, then item selectbox sorted by stock-ascending so low items bubble to the top); "Needs attention" alerts on the right showing the top 8 critical items.
  - **📊 Browse Stock** — single sortable `st.dataframe` over the full catalog. Three filter controls (name search, category, status). 🔴 / 🟡 / 🟢 status column. Scales natively to 10k+ rows via Streamlit's built-in scroll + sort.
  - **📜 Recent Changes** — last 80 events from `inventory_log`, filterable by reason (All / Orders only / Restocks only).

### 15. Admin Gating
The Inventory page exposes live stock, the restock form, and the order history — none of which should be reachable on a public URL. `db/admin_auth.py` reads `ADMIN_PASSWORD` from env and gates the page in three layers: (a) **hard-locked when unset** — the page renders an "admin disabled" message and stops, never open by default; (b) the password check uses `hmac.compare_digest` (constant time) so a timing oracle can't leak which prefix matched; (c) once unlocked, `st.session_state.admin_authenticated` carries the auth state so each interaction doesn't re-prompt, and a Lock button clears it.

### 16. Modifier Pricing and Per-Line Options in the Cart
`add_to_cart` looks up modifier prices from the menu item's `modifiers` list and passes their sum into `db/cart.py:add_item` as `modifier_upcharge`. `line_total = qty × (price + upcharge)`. Cart entries carry the upcharge for receipt rendering. The same modifier prices apply in `place_order` (the test util), so the cart and the standalone subtotal computer cannot diverge — a regression test pins them together. The LLM never sees prices: it picks modifier IDs, Python looks up the dollars.

**Per-line options.** Items also carry **options** — categorical choices like `salsa: hot`, `tortilla: flour`, `rice: brown`, `flavor: tamarind`. Unlike modifiers, options carry no upcharge; they're choices, not paid add-ons. The cart dedup tuple is `(item_id, modifiers, options)`, so two adds of the same item with different option choices stay as **separate** cart lines. "Three birria tacos: one hot salsa, one mild, one habanero" produces three distinct lines rather than collapsing to qty=3 with a single salsa. `add_to_cart` validates option keys and values against `item.options[key].choices` — unknown keys or invalid choices return a structured error so the agent self-corrects.

### 17. Idempotent Finalization
Streamlit reruns are common (browser refresh during the spinner, network blip after Stripe confirms but before save), and a naïve finalize would double-charge or double-decrement on the retry. Three layers stop that:

1. **Stable `order_id`** — reserved once when the user reaches the payment step, stored in `st.session_state`, cleared when the user edits the cart so a different cart gets a fresh ID.
2. **Stripe idempotency key** — `process_payment` passes `idempotency_key=order_id` to Stripe. A retry with the same key returns the original `PaymentIntent` rather than charging again.
3. **DB-layer dedup** — `decrement_inventory(item_id, qty, order_id)` checks `inventory_log` for the `(item_id, order_id, reason='order')` triple and short-circuits if found. `save_order` uses `INSERT OR IGNORE`. `order_exists(order_id)` lets `db/finalize.py:finalize_side_effects` bail before touching anything when the order is already persisted.

### 18. Failures Surface, Never Swallow
Post-payment side effects (save_order, decrement_inventory, send_receipt) live in `db/finalize.py:finalize_side_effects` — a pure function with no Streamlit dependencies, separated from the UI so it can be unit-tested. Stripe has already succeeded by the time it runs, so we never raise: refusing to confirm an order the customer paid for is worse than a drifted inventory row. Instead, each failure path logs the traceback through stdlib logging (`db/logging_config.py`, wired into all entry points) and appends a human-readable warning to a list. The UI renders those warnings via `st.warning` on the confirmation page so the customer and operator see exactly what needs reconciling (e.g. "Receipt email to X didn't go through. Order is confirmed — show this ID at pickup.").

### 19. Bounded LLM Calls
The Anthropic SDK's default request timeout is 600 seconds — far too long for a chat UI on a hung endpoint. The client is configured with `timeout=30.0, max_retries=2`; the retry budget covers transient rate-limit and 5xx errors with the SDK's built-in exponential backoff. When retries are exhausted, four exception classes each return a graceful customer-facing fallback ("Sorry, I'm running slow", "I'm a bit overloaded", "Trouble connecting", "I hit a problem on my end") and are recorded in the trace with the matching `stop_reason` (`timeout` / `rate_limit` / `connection_error` / `api_error`). The cart and `validated_ids` are preserved across the failed turn so the user can retry without rebuilding their order.

### 20. Centralized Restaurant Facts
Restaurant name, address, phone, neighborhood, and hours live in `data/menu.json`'s `restaurant` block and are exposed through `db/restaurant.py` as module-level constants (`NAME`, `ADDRESS`, `PHONE`, `PHONE_TEL`, `HOURS_LINE`, `SHORT_LOCATION`, `FOOTER_LINE`). The agent prompt, email templates, search FAQ-fallback, UI header, and pickup line all import from there. FAQ answers in `data/faq.json` use `{phone}` placeholders substituted at load time. A regression test scans every `.py` file under `agent/`, `db/`, `ui/`, `evals/`, and `demo.py` and fails if the literal `(305) 555-0142` is ever re-introduced outside the single defining module.

### 21. Done-Signal Python Short-Circuit
ReAct gives the LLM control over when `signal_checkout` fires. Smaller models occasionally "re-verify" the cart on a turn whose user message was nothing but "that's all" — re-running `search_menu → get_item_details → add_to_cart` and burning 5–20 extra seconds on what should be a single tool call. Prompt rules reduce this but don't eliminate it.

The deterministic fix lives in `agent/agent.py:take_order`. Before invoking the LLM loop, check if the user's message is unambiguously a checkout signal AND the cart has items. If both true, fire `signal_checkout` directly and return the canned "Perfect, heading to checkout! 🛒" response with **zero LLM calls**.

Two tiers, both conservative:

**Tier 1 — explicit done phrases:**
- Substring match against an allowlist (`"that's all"`, `"that's it"`, `"place it"`, `"place my order"`, `"go ahead"`, `"nothing else"`, `"checkout"`, `"all good"`, `"we're good"`, `"that's everything"`, `"i'm done"`).
- Whole-message match for single words (`"confirm"`, `"done"`).
- Message must be ≤ 5 words AND contain no continuation cues (`but`, `wait`, `actually`, `except`, `however`, `though`, `instead`, `also`). "that's all, but also add a Coke" → doesn't short-circuit; LLM handles.

**Tier 2 — contextual negatives:**
- Short clean negatives (`"no"`, `"nope"`, `"nah"`, `"no thanks"`, `"no thank you"`) **only when** the previous assistant message asked a completion question (`"anything else"`, `"good to go"`, `"all set"`, `"is that everything"`, `"ready to check"`, etc.).
- Alone these words are ambiguous (could deny a clarification, not close the order), so the context check is required to avoid false positives.

The combined gate (`_should_short_circuit_to_checkout`) is the only thing that decides; the rest of the loop runs unchanged when conditions don't match. Mixed messages, non-English done signals (`"eso es todo"`), empty carts, and anything the heuristic isn't sure about all fall through to the LLM.

**Result:** checkout turns drop from ~5–20 seconds and 4–8 LLM calls to ~0ms and 0 LLM calls. The behavior is also fully deterministic — no more "did the model decide to re-verify?" anxiety. Tested with 42 unit cases covering recognized signals, rejected non-signals, mixed-language fall-through, continuation-word fall-through, and a `_RaisingClient` integration test that proves the LLM was never invoked when the fast-path triggers.

### 22. Cost Optimization
The biggest cost driver at scale is per-token input cost on multi-turn orders. The system layers four mechanisms to cut it, each measurable and tested:

**1. Multi-breakpoint prompt caching.** The system prompt, tool definitions, and the last assistant message in conversation history each carry `cache_control: ephemeral` (`agent/agent.py:_SYSTEM`, `_TOOLS`, `_with_history_cache_breakpoint`). Anthropic caches the full prefix at each marker; subsequent calls within the 5-minute TTL read those tokens at **10% of normal input cost**. On a 5-turn conversation, ~80% of input tokens are cache reads. See §13 for the original system-prompt cache and the design rationale.

**2. Slim tool returns.** `search_menu` and `get_item_details` strip `description` / `dietary_tags` / `tags` from the LLM-facing payload — search-internal metadata, not used for ordering decisions. Ambiguous candidate lists are capped at 5: real customer disambiguation needs 3–5 options, more is overwhelming UX. **Measured: a 10-item "burrito" search returns ~1,350 chars instead of the ~2,700 a raw projection would produce — 50% smaller.** Search results live in conversation history for the rest of the order, so trimming them is the highest-impact single change.

**3. Skip the LLM entirely when conditions allow.** Two deterministic short-circuits handle ~15–25% of real-traffic turns at **zero LLM cost**:
- **Checkout signals** (§21) — explicit done phrases (`"that's all"`, `"place it"`, …) AND contextual `"no"` after `"anything else?"` → fire `signal_checkout` directly.
- **FAQ queries** (`_is_faq_query` in `agent/agent.py`) — clear restaurant-info questions (`"what time do you close"`, `"do you have parking"`, `"do you take credit"`, dietary questions, …) → call `search_faq` directly, return its answer. Falls through to the LLM on low-confidence matches so edge cases aren't lost.

**4. Idempotent + parallel tool calls.** The LLM batches independent tool calls in a single iteration when the prompt allows — `search_menu` × 2 in parallel for multi-item orders, `get_item_details` × 2, `add_to_cart` × 2. Combined with `validated_ids` persistence across turns (§7, §12), the cart never burns an LLM call to re-validate something already seen.

**Measured on the live 88-case Haiku eval:** cache hit rate **92.9%**, total cost **$0.39** for the full suite (352 LLM calls, 1,979,103 cache_read tokens at the 0.10× rate). The system prompt + tool definitions clear Haiku's ~2,048-token minimum cacheable prefix on the first call, so nearly every call within the 5-minute TTL hits cache. Sonnet (1,024-token minimum) sits at 92.6% on the same workload. Verified by 395 unit tests including `_RaisingClient` integration tests that fail if the LLM is invoked when a short-circuit should fire.

### 23. Latency Optimization
Three layers operate on different latency surfaces — request-floor, per-turn p50, and tail p95/p99:

**1. Bounded LLM calls** (§19). 30-second request timeout (SDK default is 600s). Graceful fallbacks for all four terminal exception types (`APITimeoutError`, `RateLimitError`, `APIConnectionError`, `APIError`). The UI cannot freeze on a hung endpoint; worst case is a friendly retry message within 30 seconds.

**2. Skip-the-LLM short-circuits drop the per-turn latency floor.** Same two short-circuits as §22 #3, viewed through the latency lens:
- **Checkout turns:** ~5–20 seconds LLM-driven → **~0ms** Python-driven.
- **FAQ turns:** ~3–5 seconds (2 LLM round trips: decide-tool + format-response) → **~100ms** (single local semantic search). At ~10–15% of traffic being info queries, this materially shifts p50 on real-traffic mixes.

**3. In-memory caches eliminate repeat work in the search pipeline.**
- The 10k-item menu is loaded once per process (`_all_items_cache`) — no DB hit per search.
- `fastembed` embeddings are computed at init and held as a numpy matrix; semantic search is a dot product (`scores = _embed_matrix @ q_vec`), not a model call per query.
- FTS5 BM25 hits the SQLite index — no full table scans.
- Per-search cost is **~150–300ms regardless of catalog size**.

**4. Parallel tool calls** (Anthropic API native). The agent issues multiple `search_menu` / `get_item_details` / `add_to_cart` calls within a single iteration when independent. "2 birria tacos + a Mexican Coke" cuts from 4 sequential iterations to 3 parallel ones — a ~25% wall-clock reduction on multi-item orders.

**5. Prompt caching also reduces compute.** Cached input tokens skip re-tokenization on Anthropic's side. Cache hits return faster than uncached requests (Anthropic claims ~10ms cache-lookup latency vs. ~1ms-per-token re-tokenization). Affects p95/p99 noticeably on long histories.

---

## Testing

395 unit tests across 12 files. None require an Anthropic or Stripe API key — terminal SDK errors are constructed from real `anthropic.APIError` subclasses with mocked `httpx.Request`/`Response`, and Stripe calls are monkey-patched. The full suite runs in **~100 seconds** locally; most of that is one cold `init_db()` call building the 10k-item catalog into SQLite + FTS5 + a fastembed index. Subsequent test files reuse the warm DB via module-scoped fixtures.

```bash
python3 -m pytest tests/ -v                     # full suite
python3 -m pytest tests/test_payment.py -v      # one file
python3 -m pytest tests/ -k "modifier" -v       # filter by name
python3 -m pytest tests/ --tb=short             # compact tracebacks
```

| Test file | What it covers |
|---|---|
| `test_tools.py` | `search_menu`, `get_item_details`, cart tools, **modifier-pricing regression** (cart subtotal must equal `place_order` subtotal for the same input) |
| `test_search.py` | `parse_price_constraint` (8 patterns), `parse_dietary_filter` (every tag), `reciprocal_rank_fusion` correctness on small fixed inputs |
| `test_evals.py` | Eval scoring layer — `_order_from_cart`, `signal_checkout`→`confirmed` status mapping, item / modifier / subtotal scoring against the cart at signal time |
| `test_tax.py` | Miami-Dade 8% math: rounding boundaries, zero order, delivery-fee composition |
| `test_delivery.py` | ZIP zone lookup (zones 1/2/3 + out-of-range), neighborhood fallback, **ZIP regex regression** (street-number must not be misread as ZIP), generic "Miami" → `needs_zip` |
| `test_payment.py` | Card-number → Stripe test token mapping (with space / dash stripping), `idempotency_key` defaults to `order_id`, declined / auth-error / generic-error all return graceful shapes |
| `test_finalize.py` | DB-layer idempotency: `order_exists`, `save_order` INSERT-OR-IGNORE, `decrement_inventory` dedupes via `inventory_log`, stock clips to 0 |
| `test_finalize_side_effects.py` | Pure `finalize_side_effects` function — clean path, idempotent re-entry, and each failure mode (save_order / decrement / email) logs with traceback and returns a human-readable warning |
| `test_agent_resilience.py` | Each terminal Anthropic exception (`APITimeoutError`, `RateLimitError`, `APIConnectionError`, generic `APIError`) → graceful fallback, correct trace `stop_reason`, cart and `validated_ids` preserved |
| `test_init_db.py` | Cold-start contract: file is created, all six tables present, canonical menu loaded, FTS index populated, inventory seeded, idempotent re-init does not wipe live orders or stock |
| `test_admin_auth.py` | `is_admin_configured` + `verify_admin_password` — hard-lock when env unset, constant-time compare, case/whitespace sensitivity, unicode passwords |
| `test_restaurant.py` | Centralized constants match `menu.json`, derived strings (`PHONE_TEL`, `SHORT_LOCATION`, `FOOTER_LINE`), **regression sentinel** scanning every `.py` file for re-introduced phone literals |

**Test-pyramid philosophy.** Most assertions live at the unit layer (pure functions, DB ops, parsing) — fast, deterministic, no API spend. Integration is covered by:

1. **End-to-end via the eval suite** (§Eval Framework) — 88 multi-turn cases that exercise the full `take_order → tool dispatch → cart → signal_checkout` path against a live Anthropic endpoint.
2. **Browser-level via Streamlit** — manual smoke test through the UI confirms the 4-step checkout flow, including the back-button / cart-edit reset of `order_id`.

We deliberately don't write Selenium / Playwright tests against Streamlit: the framework rebuilds the page on every interaction and the UI surface is intentionally thin (rendering decisions live in `_render_*` helpers; business logic lives in pure functions that *are* unit-tested).

---

## CI / Continuous Integration

GitHub Actions workflow at `.github/workflows/ci.yml`. Two jobs, both running on `ubuntu-latest` with Python 3.11 and a pip cache keyed on `requirements.txt`.

### Job 1: `test` (always runs)
- Triggers: every push to `main`, every PR to `main`, manual via the Actions tab.
- Concurrency group cancels in-flight runs on the same branch when a new commit arrives — no queue of stale builds.
- Caches `~/.cache/fastembed` so the BGE embedding model (~22 MB) doesn't re-download on each run.
- Runs the full `pytest tests/`.

### Job 2: `evals` (opt-in via secret)
Eval cases hit the Anthropic API and cost real money per run, so this job is gated:

- `needs: test` — won't run if unit tests fail.
- Restricted to push-to-main events (forks can't access secrets and we don't want to burn API spend per PR).
- **First step introspects `ANTHROPIC_API_KEY`.** If unset, every subsequent step skips and a GitHub `::notice::` annotation explains why. The job ends green with the eval gate cleanly bypassed.
- Once the secret is configured: the gate runs `python -m evals.run_evals --output eval_results.json`, uploads the JSON as an artifact (30-day retention, keyed by `github.sha`), and enforces thresholds:

| Gate | Threshold | Why |
|---|---|---|
| `pass_rate` | ≥ 90% | Allow some drift on hard edge cases; alert if regressions cluster. |
| `hallucination_rate` | == 0% | Hard gate — a confirmed order with a fabricated item ID is a correctness regression. |
| `turn_efficiency` | ≤ 1.5x | Catch loops where the agent takes 2-3x the minimum turn count (usually means the prompt or tool chain broke). |

Thresholds are deliberately lenient on the soft metrics. Tighten them once a multi-run baseline is established.

### Local CI parity
The exact pytest command CI runs is `pytest tests/ -v --tb=short`. To reproduce locally without re-downloading the embedding model:

```bash
python3 -m pytest tests/ -v --tb=short
```

---

## Observability

Three layers — structured for production but readable in development.

### 1. Stdlib logging
`db/logging_config.py:setup_logging()` is idempotent and called from all three entry points (`ui/app.py`, `demo.py`, `evals/run_evals.py`). Output goes to stderr, format includes timestamp / level / module / message:

```
2026-05-28T14:31:08 ERROR db.payment — save_payment failed for order_id=TT-ABC12345 stripe_payment_id=pi_3MtwBwLkdIwHu7ix28a3tqPa amount_cents=1399
```

Every caught exception logs with `logger.exception(...)` and a full traceback — no silent failures. Categories that get their own logger namespace:

- `agent.agent` — LLM timeout / rate-limit / connection / API errors
- `db.payment` — Stripe call failures, local payment-record write failures
- `db.finalize` — post-payment side-effect failures (save_order, decrement, email)

Override the level for one run:

```bash
LOG_LEVEL=DEBUG streamlit run ui/app.py
```

### 2. Per-turn agent trace
Every `take_order()` call returns a `trace` dict (defined in `agent/tracing.py:Trace`) capturing:

```jsonc
{
  "total_ms": 8403.2,
  "iterations": 3,
  "llm_calls": [
    {"iteration": 1, "latency_ms": 1820.4, "stop_reason": "tool_use"},
    {"iteration": 2, "latency_ms": 2105.7, "stop_reason": "tool_use"},
    {"iteration": 3, "latency_ms":  908.1, "stop_reason": "end_turn"}
  ],
  "tool_calls": [
    {"iteration": 1, "name": "search_menu",      "input": {...}, "latency_ms":  41.2, "output_chars": 287, "error": false},
    {"iteration": 2, "name": "get_item_details", "input": {...}, "latency_ms":  12.8, "output_chars": 612, "error": false},
    {"iteration": 2, "name": "add_to_cart",      "input": {...}, "latency_ms":   3.1, "output_chars": 184, "error": false}
  ]
}
```

The Streamlit UI renders this trace under each assistant message in a collapsible expander. The CLI demo prints a one-line summary. The eval runner records it per turn for failure analysis. Resilience errors are captured here too — a timeout shows up as `stop_reason: "timeout"` with no tool calls.

### 3. User-visible warnings
`_finalize_order` collects human-readable warnings from `finalize_side_effects` and `st.warning()`s them on the confirmation page. Customers see exactly what went wrong (e.g. *"Receipt email to X didn't go through. Order is confirmed — show this ID at pickup."*) — operators reading logs see the matching `logger.exception` with full traceback.

---

## Security & Threat Model

### Data flow

| Data | Where it lives | Where it doesn't |
|---|---|---|
| **Card number, CVC, expiry** | Streamlit form (form values, in-process). Stripe API (in test mode, mapped to a `pm_card_*` token before transmission). | **Never in our DB.** `payments` table stores only `stripe_payment_id`, `amount_cents`, `status`. |
| **Customer email, phone** | `orders.email`, `orders.phone` (when provided). Sent to Gmail SMTP for receipts. | Not exposed to the agent — the LLM never sees customer contact details. |
| **Order contents** | `orders.items_json` (cart at finalize time), `inventory_log.order_id` for stock reconciliation. | — |
| **API keys & SMTP password** | `.env` (gitignored). Read once at process start via `python-dotenv`. | Never logged, never returned in any tool result, never sent to the LLM. |

### What's gated

- **Inventory admin page** — `ADMIN_PASSWORD` env required to enable; without it the page is hard-locked. With it, `hmac.compare_digest` rules out timing-oracle attacks (see §15).
- **Stripe live keys** — only `STRIPE_SECRET_KEY=sk_test_...` is supported by this build. Switching to a live key without also wiring Stripe Elements (browser-side tokenization) would expose card data on the server; see §If Shipping point 3.
- **CI secrets** — `ANTHROPIC_API_KEY` is read from GitHub repository secrets and is unavailable to PRs from forks.

### What's not (and why)

- **No customer auth.** This is a single-tenant ordering UI — there are no per-customer accounts. Identity is the email captured at checkout, which is sufficient for receipt delivery but not for "log in to see your past orders" (which doesn't exist).
- **No CSRF protection on the Streamlit forms.** Streamlit's session-state model isn't form-token based; CSRF is handled by the framework's session cookie. If you reverse-proxy in front, ensure the proxy doesn't strip the cookie.
- **No rate limiting.** A bad actor could burn token spend by spamming the chat. Listed as priority #7 under §If Shipping to Production.

### `.gitignore` enforcement
Secrets (`.env`), runtime artifacts (`*.db`, `__pycache__/`, `.pytest_cache/`, `venv/`), and macOS noise (`.DS_Store`) are gitignored. `data/menu.db` is intentionally **not** tracked — every clone rebuilds it from `data/menu.json` + `data/menu_expanded.json` via `init_db()`. The `tests/test_init_db.py` cold-start test pins this contract.

---

## Eval Framework

### Why These Metrics

A good ordering agent should do exactly five things correctly: pick the right item, apply the right modifications, not invent items that don't exist, charge the right amount, and know when to confirm vs. ask. That maps directly to the metrics:

| Metric               | What failure it catches                                               |
|----------------------|-----------------------------------------------------------------------|
| Item ID Accuracy     | Agent picked wrong item or couldn't find it                           |
| Modifier Accuracy    | Modifier requested but not applied to the order                       |
| Hallucination Rate   | Agent invented an item ID or modifier ID not in the menu DB           |
| Subtotal Accuracy    | Price calculation error (tolerance: $0.02)                            |
| Status Accuracy      | Checked out when should wait, or refused a valid order                |
| Clarification Recall | Placed order without asking when input was ambiguous                  |
| Refusal Precision    | Helped with off-topic request instead of declining                    |
| Turn Efficiency      | Agent took more turns than the minimum required                       |
| Latency p50/p95/p99  | Per-turn response time distribution across all cases                  |

### How the Test Set Was Built

88 cases across 15 categories, each designed to isolate a specific failure mode. The categories span both the core ordering loop (simple, modifiers, ambiguous, refusal, multi_turn, dietary, edge) and production-traffic patterns that the core categories are too narrow to catch on their own (Spanish, info queries, conversational openers, long multi-turn flows, modifier combos, conflicting requests, allergen flows, quantity edges).

| Category             | Cases | What it tests                                                                                              |
|----------------------|-------|------------------------------------------------------------------------------------------------------------|
| **simple**           |     5 | Single/multi-item orders with no ambiguity — baseline ordering correctness                                 |
| **modifiers**        |     7 | Add-ons, exclusions, per-item modifiers on multi-item orders                                               |
| **ambiguous**        |     6 | Agent must ask exactly one clarifying question before placing                                              |
| **refusal**          |     8 | Off-topic requests (weather, math, jokes) and items not on the menu                                        |
| **multi_turn**       |    11 | Multi-message orders, post-placement modifications, out-of-stock recovery, mind changes, full reset        |
| **dietary**          |     3 | Vegan / gluten-free / dairy queries — informational, not always an order                                   |
| **edge**             |     3 | Typo recovery, price queries, restaurant info                                                              |
| **spanish**          |    10 | Spanish-only and mixed-language orders ("Quiero un taco de birria", "Can I get a carnitas taco")           |
| **info_query**       |     8 | Price queries, recommendations, menu browse, hours — agent must answer without auto-adding to cart         |
| **conversational**   |     5 | Greetings, polite openers, thanks — agent should engage rather than refuse or silently order               |
| **long_multi_turn**  |     5 | 5–6 turn flows: browse → order → modify → confirm; mid-flow remove; price-query interrupts                 |
| **modifier_combos**  |     6 | Heavy modifier combinations including paid + free, per-item modifiers in multi-item orders, qty × upcharge |
| **conflicting**      |     4 | Mutually exclusive requests ("vegan with chicken", "gluten-free burrito") — must clarify, not silently pick |
| **allergen**         |     3 | Allergen-elimination flows: "I'm allergic to X, what can I have?" → safe options → optional order          |
| **quantity_edge**    |     4 | Bulk quantities (20 tacos), word-quantities ("a dozen"), nonsensical quantities (zero, fractional)         |
| **Total**            |    88 |                                                                                                            |

**Authoring discipline.** Every case is validated programmatically before commit by `evals/validate_cases.py` — item IDs must exist in `data/menu.json`, modifier IDs must be valid for the parent item, and `expected_subtotal` must equal `Σ qty × (price + modifier_upcharge)` to within $0.02. Run before pushing test-case changes:

```bash
python3 -m evals.validate_cases
```

If you change the menu or modifier prices, the validator catches stale expected totals immediately. Exit code is 1 on any error so you can wire it into a pre-commit hook or CI step.

### What's Scoped Out (and Why)

The 88 cases cover the core production-traffic patterns but stop short of several tiers that would inflate cost or scope beyond what this assessment needs. Calling them out explicitly so the gaps are visible and the next-step work is named:

| Tier | Why it's not here | Approx. cost to add |
|---|---|---|
| **Adversarial / prompt injection** (~30 cases) | Different scoring rubric (pass = "didn't comply"), requires a separate runner and refusal-quality grading. Worth doing on every model upgrade, not on every PR. | +$0.30–1.00 per run; ~1 day to author + rubric design. |
| **Auto-augmented wording variations** (~200+ cases) | Use Claude to synthesize 5–10 paraphrases per existing case. Cheap to generate but balloons the runtime suite and adds limited novel coverage — diminishing returns past ~150 cases until production traffic shows the gaps. | One-shot generation cost ~$2; ongoing eval cost scales linearly with suite size. |
| **LLM-judge response-quality scoring** | The current eval scores structure (item IDs, modifiers, subtotal, status). It does *not* score whether the agent *said the right thing* — tone, suggesting alternatives, refusal phrasing. An LLM judge with a 5-dimension rubric closes this, at roughly 2× the per-run cost. | ~2× eval cost; ~half a day to author the rubric. |
| **Multi-replicate runs for statistical power** | At 88 cases × 1 run, pass-rate differences below ~3% are inside the noise floor. Production model comparisons need 3–5 replicates per model with consistent seeding to draw real conclusions. | 3–5× single-run cost per model under comparison. |
| **Additional Miami languages** | Spanish is covered; Haitian Creole and Portuguese are real customer languages in Miami-Dade and not tested. | ~10–20 cases per language; same authoring effort as the Spanish set. |

### What Production Scale Needs On Top

A correctness eval is necessary but not sufficient at production scale. The signals below would all need wiring before going live to real customers — they're orthogonal to the case-based eval and measure different failure modes:

- **Load and concurrency testing.** The current eval is serial — 88 cases run one after another. Production has N concurrent sessions sharing the agent loop, the SQLite DB (single-writer!), the embedding index, and the Anthropic rate limit. Latency p99 under 100 RPS looks nothing like p99 at 1 RPS.
- **Cost-per-order at realistic traffic.** Token counts per turn, cache-hit rate per session, distribution of turn counts per order. The model-comparison table assumes ~3-turn orders; long-tail catering orders blow this out.
- **Production traffic replay.** Sample 10–50 anonymized conversations per week, hand-grade the interesting ones, promote them into the regression suite. The synthetic eval is a floor; replay catches the long tail you couldn't have anticipated when authoring cases.
- **Continuous adversarial gate.** Anthropic ships model updates that can change refusal behavior in subtle ways. Run the adversarial tier (above) automatically on every model-version bump, gate the rollout.
- **Phonetic / typo coverage at customer scale.** "biria", "burito", "tcao", "tako", "mexikan koka kola" — real customer typing. Fuzz-generate variants from the canonical names and assert resolution.
- **Conversion / business metrics, not just correctness.** Order completion rate, abandonment by checkout step, average order value, repeat-order rate. A model that passes 100% on the eval but takes 18s per turn will tank conversion. Listed under §Production Metrics to Track.
- **Post-order satisfaction loop.** Optional one-question survey after order confirmation ("did we get this right?"); complaints feed back into the eval-case authoring pipeline.
- **Time-of-day and seasonal patterns.** Breakfast taco demand at 8am vs. carnitas-bowl demand at 11pm; school-night vs. game-day order shape. Static evals miss this entirely.
- **Restaurant ops integration tests.** The current system saves orders to SQLite and stops. At scale you have KDS routing, POS reconciliation, kitchen prep-time estimates, courier dispatch. Each integration is an end-to-end test in its own right.
- **Multi-restaurant / multi-tenant correctness.** If this becomes a platform, the menu / inventory / restaurant facts all become per-tenant state. They're currently module-level singletons (see §20). Tests for tenant isolation would be net-new.
- **A/B testing infrastructure.** Eval correctness is one signal; conversion lift from a prompt change is another. They don't always agree.

---

## Results

Both models evaluated against the full 88-case suite at the current code level. Reproducible:

```bash
CLAUDE_MODEL=claude-haiku-4-5-20251001 \
  python3 -m evals.run_evals --output eval_haiku.json

CLAUDE_MODEL=claude-sonnet-4-6 \
  python3 -m evals.run_evals --output eval_sonnet.json
```

| Metric                | Haiku 4.5                  | Sonnet 4.6                 |
|-----------------------|----------------------------|----------------------------|
| **Pass Rate**         | 79 / 88  (89.8%)           | **82 / 88  (93.2%)**       |
| Item ID Accuracy      | **91.8%**                  | 89.8%                      |
| Modifier Accuracy     | 93.3%                      | **100%**                   |
| Hallucination Rate    | **0%** ✅                  | **0%** ✅                  |
| Subtotal Accuracy     | 88.6%                      | **97.7%**                  |
| Status Accuracy       | **95.5%**                  | 94.3%                      |
| Clarification Recall  | **100%**                   | **100%**                   |
| Refusal Precision     | **100%**                   | **100%**                   |
| Turn Efficiency       | 1.0x  (optimal)            | 1.0x  (optimal)            |
| Latency p50           | **2,908ms**                | 5,633ms                    |
| Latency p95           | **6,553ms**                | 11,877ms                   |
| Latency p99           | **8,589ms**                | 14,431ms                   |
| Cache hit rate        | **92.9%**                  | 92.6%                      |
| Total cost (88 cases) | **$0.39**                  | ~$1.60                     |
| LLM calls             | 352                        | 372                        |

> **Hallucination rate is 0% on both** — the `validated_ids` + modifier-ID + option-choice validation guards in Python ensure the agent cannot invent an item, modifier, or option even when the model has poor instruction-following.
>
> **Latency profile favors Haiku at every percentile.** The system prompt + tool schema clears both models' minimum cacheable prefix, so cache hit rates land at 92.9% (Haiku) and 92.6% (Sonnet). With caching as the floor, Haiku's faster base inference shows up at p50 / p95 / p99. Sonnet still wins on hard-case correctness — subtotal accuracy 97.7% vs 88.6%, modifier accuracy 100% vs 93.3% — which matters more in production than raw latency for ordering flows.
>
> **Cost ratio is ~4× Haiku.** Sonnet's nominal 4× input-token premium isn't materially offset by the (similar) cache hit rate. For high-volume, latency-sensitive workloads, Haiku wins. For high-stakes correctness on hard turns, Sonnet's structural-metric edge justifies the premium.

**By category:**

| Category             | Haiku 4.5      | Sonnet 4.6     |
|----------------------|----------------|----------------|
| simple               |   5 / 5    ✅  |   5 / 5    ✅  |
| ambiguous            |   6 / 6    ✅  |   6 / 6    ✅  |
| refusal              |   8 / 8    ✅  |   8 / 8    ✅  |
| dietary              |   3 / 3    ✅  |   3 / 3    ✅  |
| edge                 |   3 / 3    ✅  |   3 / 3    ✅  |
| info_query           |   8 / 8    ✅  |   8 / 8    ✅  |
| conversational       |   5 / 5    ✅  |   5 / 5    ✅  |
| conflicting          |   4 / 4    ✅  |   4 / 4    ✅  |
| quantity_edge        |   4 / 4    ✅  |   4 / 4    ✅  |
| modifier_combos      |   6 / 6    ✅  |   6 / 6    ✅  |
| modifiers            |   6 / 7        |   6 / 7        |
| spanish              |   8 / 10       |   8 / 10       |
| multi_turn           |   9 / 11       |   9 / 11       |
| allergen             |   2 / 3        |   **3 / 3** ✅ |
| long_multi_turn      |   2 / 5        |   **4 / 5**    |
| **Total**            | **79 / 88**    | **82 / 88**    |

10 of 15 categories are 100% on both models. The mid-tier categories split unevenly: Sonnet handles long flows and the allergen safety question better; modifiers, multi_turn, and Spanish tie between the two. Haiku's failures cluster on 5+ turn flows — modifier attribution and intermediate-state confusion are where Sonnet's stronger reasoning earns its premium.

### Failure overlap

```
Both fail (5):        tc_10, tc_27, tc_51, tc_53, tc_70
Haiku-only fail (4):  tc_43, tc_67, tc_71, tc_84   ← Sonnet passes these
Sonnet-only fail (1): tc_38                         ← Haiku passes this
```

The 5 shared failures cluster around three known limitations:

- **Semantic vocabulary gaps (2):** `tc_10` ("carne asada burrito" → canonical "California Burrito"), `tc_53` ("agua" → canonical "Bottled Water"). Customer's term doesn't match the canonical name; fuzzy + semantic search doesn't bridge it reliably. Fix: small synonym table in `db/search.py`.
- **Clarification-turn double-add (2):** `tc_27`, `tc_70` — when search returns ambiguous and the customer disambiguates ("birria"), the agent adds the item twice across the two turns. Cart ends at 2× expected quantity on BOTH models. Fix: cart-defense layer that detects same-item re-add at qty=1 with identical modifiers.
- **Spanish cheese-quesadilla flow (1):** `tc_51` — Spanish disambiguation ("quesadilla de queso") followed by a confirm turn; both models occasionally end at `in_progress` instead of firing `signal_checkout`. Multi-language disambiguation chains are a known soft spot.

Haiku's four model-specific failures (`tc_43` full-order reset, `tc_67` long-browse, `tc_71` long-multi-category, `tc_84` celiac-then-order) all hit on 5+ turn flows — the same complexity ceiling that shows up in the category breakdown.

---

## Model Selection

Two models were evaluated: **Claude Haiku** and **Claude Sonnet**, both from Anthropic's Claude 4 family.

**Claude Haiku 4.5** (`claude-haiku-4-5-20251001`) is Anthropic's fastest, most cost-efficient model. It's optimized for high-throughput tasks with structured outputs — tool use, classification, extraction. At roughly 1/8th the cost of Sonnet, it's the right default for development, CI, and high-volume production workloads where latency and cost matter more than nuanced reasoning.

**Claude Sonnet 4.6** (`claude-sonnet-4-6`) is the mid-tier model — stronger reasoning and instruction following than Haiku, significantly cheaper than Opus. It handles edge cases, ambiguous phrasing, and multi-step reasoning more reliably. For a customer-facing ordering agent where a wrong response has real consequences (wrong order placed), Sonnet's stronger instruction adherence is worth the cost premium.

| Attribute              | Haiku 4.5                                                            | Sonnet 4.6                                                          |
|------------------------|----------------------------------------------------------------------|---------------------------------------------------------------------|
| Model ID               | `claude-haiku-4-5-20251001`                                          | `claude-sonnet-4-6`                                                 |
| Pass rate (88 cases)   | 79/88 (89.8%)                                                        | **82/88 (93.2%)**                                                   |
| Cost per 88 cases      | **$0.39**                                                            | ~$1.60                                                              |
| Cost ratio             | 1×                                                                   | ~4× Haiku                                                           |
| p50 latency            | **2,908ms**                                                          | 5,633ms                                                             |
| p95 latency            | **6,553ms**                                                          | 11,877ms                                                            |
| p99 latency            | **8,589ms**                                                          | 14,431ms                                                            |
| Cache hit rate         | **92.9%**                                                            | 92.6%                                                               |
| Hallucination          | 0% ✅                                                                | 0% ✅                                                               |
| Subtotal accuracy      | 88.6%                                                                | **97.7%**                                                           |
| Modifier accuracy      | 93.3%                                                                | **100%**                                                            |
| Instruction following  | Strong on mechanical tool chains; weaker on long multi-turn          | Stronger reasoning on edge cases, slightly better on Spanish + long |
| Best at                | Latency at every percentile, cost-sensitive throughput               | Structural correctness on hard / long cases                         |

**Picking a default.** The choice bifurcates cleanly along traffic shape:

- **Haiku as default** when latency and per-call cost matter most. Beats Sonnet at p50/p95/p99 and runs at ~4× lower cost. The 3.4-percentage-point pass-rate gap concentrates entirely on 5+ turn flows; on 1–3 turn orders (the bulk of real traffic) it's invisible.
- **Sonnet as default** when structural correctness matters more than latency or cost. Modifier accuracy 100% (vs 93.3%) and subtotal accuracy 97.7% (vs 88.6%) mean fewer mis-charged customers. For a customer-facing ordering agent where a wrong total is a real business problem, the 4× premium is defensible.
- **The hybrid** (Haiku default + Sonnet escalation on detected hard cases) is the strongest design point: Haiku's latency floor with Sonnet's tail correctness. The eval shows four cases Sonnet uniquely solves (`tc_43, tc_67, tc_71, tc_84` — all 5+ turn flows), so a simple heuristic — "escalate to Sonnet at turn ≥ 5" or "escalate on modifier-attribution detected in earlier turns" — captures most of the lift.

**Cache hit rate dominates the cost math.** With the system prompt + tool schema sized to clear both models' minimum cacheable prefix, both land near 92% hit rate within the 5-minute TTL. Sonnet's nominal 4× input-token premium isn't offset by the (already-high) cache rate, so the cost gap is structural rather than something prompt tuning can close further. The lever for cost reduction is fewer LLM calls (intent router, distillation — see §Reducing Cost at Scale), not better caching.

---

## Engineering Notes

The system relies on a few recurring patterns that aren't obvious from reading any single file. Calling them out here so future contributors don't accidentally undo them.

---

### Defensive layers: prompt instructs, Python enforces

Every correctness rule in the system has two implementations: one in the prompt (so the model usually does the right thing) and one in Python (so it can't do the wrong thing). The prompt is the fast path; the Python guard is the contract.

- **Item ID validation.** Prompt tells the agent to call `get_item_details` before `add_to_cart`. The dispatcher tracks `validated_ids` and blocks any `add_to_cart` whose `item_id` hasn't passed through `get_item_details` — returning an error string the agent can self-correct against.
- **Modifier ID validation.** Prompt enumerates valid modifier IDs via `get_item_details`. `add_to_cart` and `update_item_modifiers` cross-check submitted modifier IDs against the menu's modifier list and reject unknown ones. A silent `$0` fallback would let the customer pay nothing for a real `$3` modifier when the model hallucinates `add_extra_meat` instead of `extra_meat`.
- **Option key/value validation.** Same pattern for choice options (salsa, tortilla, protein, flavor). `add_to_cart` validates option keys against `item.options` and option values against the menu's `choices` list.
- **Cart re-add detection.** `add_to_cart` returns `new_line: bool` plus a `hint` when an existing line was incremented. The agent reads the hint and can revert via `set_item_quantity` when the increment wasn't customer-intended (recurring failure mode on clarification turns).
- **Embedding dimension validation.** `_load_index_from_db` rejects any vector that isn't shape `(384,)` and triggers a clean rebuild. Prevents a one-time `fastembed` failure from permanently poisoning the on-disk index with zero-vectors.

The pattern lets the prompt drift without correctness regressions. Every model upgrade is one less risk to manage.

---

### Hybrid search with disambiguation in Python, not the LLM

`search_menu` returns a `match` flag (`"exact" | "ambiguous" | "none"`). The LLM acts on the flag — it doesn't apply sorting heuristics or pick winners itself. The decision tree (clear-winner fast-path → food-type filter → descriptor match → ambiguous fallback) lives in `agent/tools.py:search_menu`, where it's testable in isolation and deterministic.

The clear-winner fast-path has three rules tuned for the 10k-item catalog where canonical items compete with synthetic variants:

1. **Strong score advantage** — top fuzzy match `≥ 85`, gap to runner-up `≥ 15`.
2. **Same score, shorter canonical** — top `≥ 85`, gap `< 5`, runner-up name `≥ 5` chars longer.
3. **Canonical beats synthetic at near-equal score** — canonical `≥ 80`, runner-up synthetic, gap `≥ -2`.

Score is `max(token_sort_ratio, partial_ratio)` so informal abbreviations ("chips and guac") match `partial_ratio`-style. Canonical menu items are scored separately and merged into the candidate pool, so they don't get squeezed out of the fuzzy top-15 by 100+ synthetic variants of the same dish.

---

### Cart shape: `(item_id, modifiers, options)` dedup tuple

The cart line key is the full tuple. Two adds with the same item but different option choices (one taco with hot salsa, one with mild) stay as **separate** lines — important for per-item customization. Same item + same modifiers + same options merges into one line with incremented quantity.

Three tools mutate existing lines:
- `update_item_modifiers(item_id, modifiers[, options])` — replace the modifier list.
- `set_item_quantity(item_id, qty[, options])` — replace quantity (the "make it 2" semantic).
- `remove_from_cart(item_id[, options])` — remove a line.

The `options` argument is optional when only one line matches the `item_id`; required when multiple lines share an `item_id` (per-line options). Missing in the ambiguous case returns an error listing the existing lines so the agent disambiguates.

---

### Idempotent post-payment finalization

Streamlit reruns are common (browser refresh during the spinner, network blip after Stripe confirms but before save). A naïve finalize would double-charge or double-decrement. Four layers stop that:

1. **Stable `order_id`** — reserved once when the user reaches Step 4 (payment), stored in `st.session_state`, cleared on "Edit cart" so a changed cart gets a fresh ID.
2. **Stripe idempotency key** — `process_payment` passes `idempotency_key=order_id`. A retry returns the original `PaymentIntent` instead of creating a new charge.
3. **Inventory dedup** — `decrement_inventory` checks `inventory_log` for `(item_id, order_id, reason='order')` and short-circuits if found.
4. **Order-exists early return** — `_finalize_order` skips `save_order`, inventory loop, and receipt email when `order_exists(order_id)` is true.

Each layer is independently sufficient; together they survive any rerun pattern.

---

### Failures surface, never swallow

Post-payment side effects (`save_order`, `decrement_inventory`, `send_receipt`) live in `db/finalize.py:finalize_side_effects` — a pure function with no Streamlit dependencies. Stripe has already succeeded by the time it runs, so we never raise: refusing to confirm a paid order is worse than a drifted inventory row.

Each failure path:
- Logs the traceback via stdlib logging (`logger.exception`).
- Appends a human-readable warning to a list returned from the function.

The UI renders those warnings via `st.warning` on the confirmation page (e.g. *"Receipt email didn't go through. Order is confirmed — show this ID at pickup."*). Customer sees what needs reconciling; operator reading logs sees the full traceback.

The same pattern applies to tool exceptions in the dispatcher — `logger.exception` captures the full trace before returning the error string to the LLM.

---

### Skip the LLM when conditions allow

Two short-circuits skip the agent loop entirely:

- **Done-signal** (`agent/agent.py:_should_short_circuit_to_checkout`) — if the user's message is unambiguously a checkout signal and the cart has items, fire `signal_checkout` directly. Two tiers: explicit phrases (`"that's all"`, `"checkout"`, ...) and contextual negatives (`"no"` / `"nope"` when the previous assistant message asked a completion question). Both gates conservative: any continuation word (`but`, `wait`, `actually`) blocks; any ambiguity falls through to the LLM.
- **FAQ query** (`_is_faq_query`) — high-confidence restaurant-info queries (`"what time do you close"`, `"do you have parking"`, dietary questions) skip the agent and call `search_faq` directly. Low-confidence matches fall through.

Combined coverage is ~15–25% of real-traffic turns at zero LLM cost and ~100ms latency. Pinned by `_RaisingClient` integration tests that fail if the LLM is touched when a short-circuit should fire.

---

### Refusal handling is cart-aware

Off-topic requests have two regimes:
- **Empty cart**: agent uses the `REFUSED:` prefix. Status becomes `refused`, session can cleanly end — nothing to lose.
- **Non-empty cart**: agent gives a brief redirect without the prefix. Status stays `in_progress` so the customer's order survives a tangential question. The UI has a defense-in-depth safety net: even if the model slips and tags `REFUSED:` mid-order, the cart-preserving recovery flow keeps the order intact and lets the customer continue with one click.

Items not on the menu are not refusals — the agent says we don't carry the item AND suggests a closest-match real menu alternative in the same response. Out-of-stock follows the same rule: tell the customer it's unavailable AND propose a concrete substitute (shrimp taco → fish taco, etc.).

---

### Input validation that breaks production

A few input-parsing details that would silently misbehave without explicit handling:

- **ZIP code extraction.** `check_delivery` scans all 5-digit runs in the address via `re.findall` and prefers any that match a known Miami-Dade ZIP, falling back to the last run for the rejection message. Without this, addresses like *"19501 Biscayne Blvd, Aventura, FL 33180"* would pick the street number `19501` instead of the real ZIP `33180`.
- **Negative dietary tags.** Menu items use inclusion markers (`contains_dairy`, `contains_gluten`), not exclusion markers. `items_matching_dietary` inverts the filter for any `-free` tag: dairy-free returns items *without* `contains_dairy`. Generalizes to nut-free, soy-free, etc. once the menu adds the corresponding `contains_X` tags.
- **Streamlit markdown `$` escaping.** Cart line renders escape `$` as `\$` because Streamlit's markdown treats `$...$` as inline LaTeX. Two unescaped dollar signs on one line (line total + modifier upcharge) would otherwise render as broken math.

---

### What the eval framework doesn't catch

The eval scores structure (item IDs, modifiers, options, subtotal, status). It doesn't verify response text quality — whether the agent says the right thing in natural language, suggests alternatives gracefully, or refuses with the right tone. An LLM-judge scoring rubric (Claude scoring each response on a 5-dimension scale) would close this gap and is the highest-leverage next addition to the eval suite.

---

## Edge Cases & Failure Modes Handled

- **Typo recovery** — "birria tcao" resolves silently to Birria Taco. The agent picks the item and adds it without mentioning the typo.
- **Spanish menu vocabulary** — "pollo" = chicken, "carne/asada" = beef, "camarón" = shrimp. Customers can use either language.
- **Disambiguation without over-asking** — "birria taco" with multiple candidates: food-type filter drops Burrito and Bowl, descriptor matching checks whether "birria" appears in exactly one remaining name — it does, so `match: "exact"` and no question is asked. "I want a burrito" with 4 burritos: no descriptor resolves it, so `match: "ambiguous"` and one question is asked.
- **Out-of-stock handling** — `get_item_details` returns `available: false`. Agent informs customer and stays in `in_progress`.
- **Required vs optional options** — Required options (protein on Build Your Own Burrito, flavor on Jarritos) are collected before adding to cart. Optional options (tortilla type, salsa heat) use defaults silently.
- **Per-line option choices preserved** — "Three birria tacos: one hot salsa, one mild, one habanero" creates three distinct cart lines (one per salsa) rather than collapsing to qty=3 with a single choice. Cart dedup is `(item_id, modifiers, options)`. Same item + same options still merges into one line with incremented quantity.
- **"X-free" dietary requests** — "Do you have dairy-free options?" / "lactose intolerant" / "non-dairy" all resolve through the dietary filter. Negative-tag mode returns items WITHOUT `contains_X`, so the answer matches what's actually safe rather than relying on the menu to mark every safe item with a `dairy-free` tag. Generalizes to `gluten-free` and any future "X-free" once synonyms are added.
- **Off-topic refusal** — Math, weather, jokes, code: agent begins response with `REFUSED:` sentinel (stripped before display), status set to `refused`. Not-on-menu requests are handled differently — the agent says "We don't carry X" and stays `in_progress`.
- **Item naming integrity** — Customer asks for a "chicken sandwich", agent finds Tinga Torta. It calls it "Tinga Torta", never "chicken sandwich option". Menu item names are never replaced with the customer's informal phrasing.
- **Empty cart checkout** — `signal_checkout` returns an error if the cart is empty. Status stays `in_progress`. Enforced at the Python layer.
- **Delivery zone validation** — ZIP codes and neighborhood names outside the ~10-mile delivery radius return `deliverable: false`. Minimum order for delivery is $10.
- **Address with 5-digit street number** — "19501 Biscayne Blvd, FL 33180" picks the trailing real ZIP (33180), not the street number. Regression-tested.
- **Payment failure handling** — Declined card, insufficient funds, expired card, wrong CVC: Stripe returns a specific error, displayed to the user. Order is not saved; cart remains intact for retry.
- **Hung LLM endpoint** — 30-second request timeout fires; agent returns a graceful fallback message; cart and `validated_ids` preserved so the customer can retry without rebuilding their order.
- **Rate-limited LLM** — SDK retries with exponential backoff up to the configured budget; if still rate-limited, agent shows "I'm a bit overloaded right now — please try again in a moment" and the trace records `stop_reason: "rate_limit"`.
- **Streamlit rerun during the payment spinner** — `order_id` is stable across the rerun, Stripe's idempotency key returns the original `PaymentIntent`, `decrement_inventory` dedupes via `inventory_log`, `_finalize_order` short-circuits via `order_exists()`. No double-charge, no double-decrement.
- **Receipt email fails after payment succeeds** — order is still saved, inventory still decremented, customer sees a visible warning on the confirmation page ("Receipt email didn't go through — show this ID at pickup") and the failure is logged with full traceback.
- **Modifier with unknown ID** — `add_to_cart` returns a structured error listing the valid modifier IDs for the item; the agent self-corrects on the next iteration. Silently dropping unknown modifiers to $0 (the obvious-but-wrong alternative) would let the customer pay nothing for a real $3 modifier when the model hallucinates `add_extra_meat` instead of `extra_meat`.
- **Unknown option key or invalid choice** — same pattern: `add_to_cart` errors with the valid options/choices for the item. The model corrects on the retry.
- **Concurrent restart vs. live state** — `init_db()` is idempotent; running it on an existing DB with active orders and decremented inventory preserves both (`INSERT OR IGNORE` on menu items, no `DELETE` on orders/inventory). Pinned by `tests/test_init_db.py::TestIdempotentReInit`.

---

## Known Limitations

### Architecture & Concurrency

**Thread safety.** The `_initialized` flag in `db/setup.py` is a module-level global — not thread-safe. Fine for this single-threaded demo; breaks immediately under concurrent requests. Fix: move `init_db()` to an app-startup lifecycle hook so initialization happens once before any requests are served.

**No streaming.** `take_order()` is a blocking call — the full ReAct loop runs before anything is returned. Perceived latency is wall-clock time for the full loop (2–4 LLM calls). Fix: convert to a generator using the Anthropic streaming API; stream tokens to the UI via SSE.

### Search & Product Logic

**No combo discount detection.** The cart sums individual item prices with no awareness of bundle pricing. "2 birria tacos + a Coke" won't trigger a combo discount — there are no combo rules in the data. Fix: add a deterministic post-processing step in `_finalize_order` that cross-references cart contents against combo rules before computing the subtotal.

### Eval Coverage

**Spanish covered; other Miami languages not.** The `spanish` category has 10 cases (Spanish-only and mixed-language). Haitian Creole and Portuguese — both real customer languages in Miami-Dade — are not tested.

**Response text quality not scored.** The eval framework measures structural correctness — item IDs, modifiers, subtotal, status. It doesn't verify that the agent says *the right thing* in natural language (suggesting alternatives when an item is out of stock, etc.). An LLM-judge scoring rubric would close this gap; see §Eval Framework → *What's Scoped Out*.

**No adversarial or load-tier coverage.** Prompt injection, jailbreak attempts, and concurrent-session latency are not in the suite. Both are listed in *What's Scoped Out* with the cost-to-add and the production-scale requirements that justify them.

---

## If Shipping to Production

### Agent Pattern at Scale

The current ReAct loop is the right primitive for this product. Most turns are short (1–3 tool calls), the next action genuinely depends on the last tool result, and the customer-facing UI needs to recover gracefully from ambiguity ("I want a burrito" → *which one?*) — which is exactly what ReAct's iterative re-decision is good at. A plan-and-execute pattern would have to either bake those branches into the plan (defeating the simplification) or replan on every ambiguity (which *is* ReAct in disguise).

Three things in the existing design quietly do the work a planner would otherwise do, which is why we can stay on ReAct without paying a flexibility tax:

- The mandatory tool chain in the prompt + `validated_ids` Python guard fixes the "plan" for a single add at `search → get_item_details → add_to_cart` (§7).
- Python-side disambiguation in `search_menu` collapses the decision tree before the LLM sees results — the `match: "exact" | "ambiguous" | "none"` flag is a pre-baked routing decision (§6).
- `validated_ids` persisted across turns lets follow-up modifications skip the search/validate round-trip entirely (§12).

**Where ReAct will stop being the right pattern:**

1. **Bulk orders / catering.** "20 lunches: 5 vegan, 5 gluten-free, 10 standard, delivered to one address." ReAct loops sequentially — 20× search, 20× lookup, 20× add. A planner could decompose into one classification pass and parallel item lookups. This is a real product gap, not a hypothetical.
2. **High concurrency cost.** At 2–3 LLM calls per customer turn × N concurrent sessions, the input-token bill is meaningful even with prompt caching. A planner that averages ~1 call per turn on the happy path is a 50–66% input-token reduction at scale.
3. **Tool count growth.** The system prompt grows with every tool registered. At 9 tools, the cost is invisible; at 30+, ReAct's "every iteration, decide which of N tools" gets expensive in tokens and slow in latency. That's the point where hierarchical routing (intent classifier → specialist sub-agent) starts winning.

**Strategy when those pressures arrive — layer, don't rewrite:**

- **Intent router as a first hop.** A cheap Haiku call classifies the incoming utterance into `{simple_order, modify_cart, faq, bulk_order, refusal}` and dispatches accordingly. `simple_order` (the ~95% case) keeps the current loop untouched. `faq` skips the agent loop entirely — direct `search_faq` call, one fewer LLM round-trip. `bulk_order` enters a planner agent that decomposes the request and dispatches item lookups in parallel. The current architecture already returns `status` deterministically from a tool trace, so adding a router upstream is additive — no breaking change.
- **Parallel tool calls inside the current loop.** Anthropic's API supports parallel tool calls; the agent's response to "2 birria tacos and a Mexican Coke" should issue `get_item_details(taco_birria)` and `get_item_details(drink_coke_mexican)` in the same iteration, not serially. If the prompt isn't already pushing this hard enough, that's a one-line prompt change with a 30–40% latency win on multi-item orders — much cheaper than a pattern rewrite.
- **Streaming responses.** Listed below in the priority list. Cheaper than a pattern change and gives the perceived-latency win that would otherwise motivate the rewrite.

**Bottom line:** ReAct is correct for the agent we have. The next scaling bottleneck isn't the pattern — it's parallelism *within* the loop and intent routing *around* it. Build the planner only where the bulk-order use case actually appears in product traffic.

---

### Reducing Cost at Scale

The cost optimizations from §22 — multi-breakpoint caching, slim tool returns, deterministic short-circuits for checkout and FAQ, parallel tool calls — are already in production. Layer the items below in order of cost-per-effort once the data justifies the complexity:

1. **Intent router as a first hop.** One cheap Haiku classifier call routes incoming utterances to specialist sub-paths: `simple_order` → ReAct loop (current path), `info_query` → direct FAQ (already partially covered by the FAQ short-circuit), `bulk_order` → planner agent that decomposes in parallel, `refusal` → cheap one-shot response, `conversational` → static greeting. At ~20% info+refusal+conversational traffic share, this eliminates ~30% of LLM calls. See §Agent Pattern at Scale above.

2. **Production traffic replay.** Sample 50 real conversations per week, count LLM calls per intent, identify the top three wasteful patterns. Fix them at the prompt or Python layer (not by switching models). Same playbook as the in-loop short-circuits and disambiguation-in-Python pattern (see §Engineering Notes) — turn LLM behavior into deterministic Python where the pattern is mechanical enough.

3. **Cache-warming for shared prefixes at multi-tenant scale.** Anthropic's prompt cache is per-org, not per-customer; a busy restaurant naturally keeps the cache warm. At low traffic per restaurant, a periodic keepalive request (one cheap call every 4 minutes) holds the cache TTL open. Worth it when concurrent active sessions × cache misses × token rate exceeds the cost of the keepalive.

4. **Model routing on demand, not preemptively.** Default to Haiku — 89.8% pass-rate with 0% hallucination and lower latency at every percentile. *Escalate* to Sonnet on the 4–5 turn-count threshold or when prior turns show signs of modifier mis-attribution — those are the cases where Sonnet's structural-correctness edge (subtotal 97.7% vs 88.6%) earns the premium. Don't ship a preemptive classifier; ship the heuristic-driven escalation. Routing logic is itself a cost line.

5. **Bulk-order planner.** If catering becomes a real product, ReAct's sequential loop is ~20× too expensive on 20-item orders. A planner that decomposes into parallel item lookups + a single bulk-add is the right architecture. Don't pre-build it — build when the use case shows up.

6. **Rate limiting per session.** Prevent runaway LLM costs from a single bad actor — token budget per session per hour, enforced before the LLM call goes out.

7. **Distillation (long-term).** Once production has labelled data on the agent's tool-chain decisions, train a smaller specialized model on those traces. Production paths that don't need general reasoning (the ~95% mechanical case) could run on a 1–3B distilled model at ~1/10 of Haiku's cost.

### Reducing Latency at Scale

Building on the optimizations in §23 — short-circuits, in-memory caches, parallel tool calls, bounded timeouts — production scaling targets p95/p99 and perceived latency:

1. **Streaming responses.** Convert `take_order` into a generator that yields tokens as the Anthropic API streams them. The UI displays text as it arrives. Doesn't reduce wall-clock latency but cuts **perceived** latency dramatically — a customer seeing "Two birria tacos…" appear after 400ms feels much faster than a blank spinner for 4 seconds even if the total is similar.

2. **Move the agent loop to an async task queue.** Celery + Redis. The web worker returns a `task_id` immediately; the ReAct loop runs in the background; the UI subscribes to a Server-Sent Events stream for incremental updates. Prevents HTTP-level timeouts and frees web workers to handle concurrent sessions.

3. **Pre-warm `fastembed` on app start.** The first semantic search downloads the BGE model (~22 MB) and takes ~5–10 seconds. Move this to an app-startup lifecycle hook so the first customer doesn't pay the warmup tax. CI already caches the model directory; production should do the same and load it on boot.

4. **LRU cache on `search_menu`.** Common queries (`"vegan options"`, `"birria taco"`, `"what's popular"`) repeat across customers. 60-second TTL, invalidated on inventory changes for items in the cached result. Cuts repeat-search latency from ~150–300ms to ~1ms; meaningful at high concurrency where the same query hits the embedding model dozens of times per minute.

5. **Per-region Anthropic / Bedrock endpoints.** When Anthropic offers regional inference (or via Bedrock / Vertex regional endpoints), route based on customer location. Saves ~50–100ms per LLM call on transcontinental requests. At p99 with multiple LLM calls per turn, this compounds.

6. **Lower `MAX_ITERATIONS` based on production data.** The current cap is 8; the saved eval shows most orders complete in 2–4 iterations. Track the iteration-count distribution in production (already in the trace). If 99% complete by iteration 5, lower the cap. Doesn't help happy-path latency but caps the worst case and removes a silent-failure surface.

7. **Lift more prompt rules into Python.** Where prompt rules are mechanical (e.g., "always call `search_menu` before adding"), lift them into the dispatch layer where they execute in microseconds instead of via LLM reasoning. §7 (`validated_ids`) and §21 (done-signal short-circuit) already do this for two rules; the same pattern can move others — for example, "Spanish menu vocabulary mapping" could become a deterministic preprocessor that maps `pollo → chicken` before the search_menu call.

### Infra Priorities

In order of priority:

1. **Swap SQLite for PostgreSQL** — concurrent writes, connection pooling via pgbouncer, proper indices on `orders.created_at`. The `_initialized` flag approach breaks immediately under load.

2. **Add `session_id` to `take_order`** — scope orders and conversation history to a session. Store cart + `validated_ids` in Redis with TTL so the cart survives page refreshes and network drops.

3. **Replace Streamlit payment form with Stripe Elements** — card data goes browser → Stripe directly, bypassing the server entirely. The `_resolve_payment_method` test token mapping is a test-mode convenience; in live mode, Stripe.js handles tokenization.

4. **Move agent loop to async task queue** — Celery + Redis so long-running LLM calls (3–4 iterations × 2–3s each) don't block web workers. (Also covered as a latency win above.)

5. **Streaming responses** — SSE from Anthropic API through to the UI. (Also covered above as the primary perceived-latency lever.)

6. **Structured (JSON) logging** — stdlib logging is wired, but the format is human-readable. Switch to one JSON line per tool call: `{tool, args, result_chars, latency_ms, session_id, order_id}` for production grep / dashboards.

---

### Production Metrics to Track

**Business**
- **Order completion rate** — % of sessions that reach payment confirmation. Primary health signal.
- **Abandonment by checkout step** — which step loses the most customers (fulfillment, delivery, review, payment).
- **Average order value** — subtotal per confirmed order.

**Agent Correctness (sampled)**
- **Live hallucination rate** — on a random sample of completed carts at signal_checkout, check that every item ID exists in the DB. Should be 0%. The `validated_ids` state machine makes this structurally hard, but prompt drift or a model update could break it.
- **Tool chain enforcement trigger rate** — how often `validated_ids` blocks an `add_to_cart` call and forces a self-correction. Should be near 0 in steady state.
- **Refusal rate by reason** — off-topic vs. not-on-menu vs. out-of-stock, tracked separately.

**Performance**
- **Per-turn latency p50 / p95 / p99** — track separately for single-tool turns (info queries) vs. full search → lookup → add flows. Don't aggregate — they have different distributions and different SLAs.
- **ReAct loop iteration count distribution** — what % of orders resolve in 3 iterations vs. 5+. Any session hitting the 8-iteration cap is a silent failure.
- **Tool call latency breakdown** — semantic search, FTS5, DB lookup, cart update measured separately.

**Cost**
- **Tokens per order** — input / output / cache-hit broken out. Cache hit rate directly reflects prompt caching effectiveness.
- **LLM cost per order** — estimated from token counts using Anthropic pricing. Essential for margin analysis.

---

## Repository Structure

```
talkin-tacos/
│
├── agent/                        # Core agent logic
│   ├── agent.py                  # take_order() — ReAct loop, validated_ids state machine, status derivation
│   ├── prompts.py                # System prompt — ordering rules, refusal sentinel, tool chain instructions
│   ├── tools.py                  # All tool implementations: search_menu, search_faq, get_item_details,
│   │                             #   add_to_cart, update_item_modifiers, set_item_quantity,
│   │                             #   remove_from_cart, get_cart, signal_checkout, place_order
│   ├── tool_schemas.py           # Anthropic tool definitions (9 tools — place_order excluded from agent)
│   └── tracing.py                # Trace dataclass — records per-iteration LLM + tool call latency
│
├── db/
│   ├── setup.py                  # SQLite init, FTS5 + inventory schema, menu seeding, save_order, save_payment, order_exists
│   ├── search.py                 # Hybrid search: semantic (fastembed) + FTS5 BM25 + rapidfuzz, merged via RRF
│   ├── cart.py                   # Pure cart operations: add_item, remove_item, get_subtotal — includes modifier upcharges
│   ├── tax.py                    # Miami-Dade 8% tax calculation (7% FL state + 1% county surtax)
│   ├── delivery.py               # ZIP/neighborhood → delivery zone, fee ($2.99/$4.99/$7.99), ETA
│   ├── payment.py                # Stripe payment: test card → pm_card_* token, PaymentIntent with idempotency_key, save_payment
│   ├── finalize.py               # Pure post-payment side-effects: save_order, decrement_inventory, send_receipt — returns warnings
│   ├── admin_auth.py             # Constant-time ADMIN_PASSWORD check for the Inventory page (hard-locked when unset)
│   ├── restaurant.py             # Single source of truth: NAME, ADDRESS, PHONE, HOURS — loaded from menu.json
│   ├── logging_config.py         # setup_logging() — stdlib logging, called from all entry points
│   └── email.py                  # Gmail SMTP receipt sender — references db.restaurant constants
│
├── data/
│   ├── menu.json                 # Canonical menu (29 real items — source of truth for modifiers/options + restaurant facts)
│   ├── menu_expanded.json        # Full 10,060-item generated catalog (proteins × styles × sizes)
│   ├── faq.json                  # Restaurant FAQ ({phone} placeholders substituted from db.restaurant at load)
│   └── menu.db                   # SQLite DB — auto-generated, gitignored
│
├── evals/
│   ├── test_cases.json           # 88 test cases — turns, expected items, modifiers, subtotals
│   ├── metrics.py                # TurnResult, CaseResult, score_case() — scores cart at signal_checkout time
│   ├── run_evals.py              # CLI runner — threads cart + validated_ids across turns
│   └── validate_cases.py         # Pre-commit validator: schema, real IDs, arithmetic
│
├── tests/                        # 395 unit tests — no API key required, no Stripe calls
│   ├── test_tools.py             # search_menu, get_item_details, cart, modifier-pricing regression
│   ├── test_evals.py             # Eval scoring layer (signal_checkout → cart-as-order, status mapping)
│   ├── test_finalize.py          # Idempotency: order_exists, decrement_inventory dedup, save_order INSERT OR IGNORE
│   ├── test_finalize_side_effects.py  # Pure finalize: clean path + each failure mode + logging
│   ├── test_agent_resilience.py  # Timeout/RateLimit/Connection/APIError → graceful fallback, logged
│   ├── test_tax.py               # Miami-Dade 8% math, breakdown shape
│   ├── test_delivery.py          # Zone lookup, ZIP regex regressions, neighborhood fallback
│   ├── test_payment.py           # Card → token mapping, idempotency_key, declined / auth / generic error paths
│   ├── test_search.py            # parse_price_constraint, parse_dietary_filter, reciprocal_rank_fusion
│   ├── test_init_db.py           # Cold-start: tables, menu loaded, FTS populated, idempotent re-init
│   ├── test_admin_auth.py        # is_admin_configured + verify_admin_password (hard-lock when unset)
│   └── test_restaurant.py        # Centralized constants + regression sentinel for hardcoded phone strings
│
├── ui/
│   ├── app.py                    # Streamlit chat UI + 4-step checkout (idempotent finalize, surfaced warnings)
│   └── pages/
│       └── 1_Inventory.py        # Admin page — gated behind ADMIN_PASSWORD; live stock, restock, change log
│
├── scripts/
│   └── generate_menu.py          # Generates menu_expanded.json — 10,060 items across 24 categories
│
├── .github/
│   └── workflows/
│       └── ci.yml                # pytest on every push/PR; opt-in eval gate (skips when ANTHROPIC_API_KEY unset)
│
├── demo.py                       # Terminal CLI demo — multi-turn conversation walkthrough
├── setup.sh                      # One-command setup: venv + pip install + DB Browser for SQLite
├── requirements.txt              # Python dependencies
├── .env.example                  # API key + model + Stripe key + ADMIN_PASSWORD config template
├── eval_haiku.json               # Full Haiku eval run — 88 cases, current code
├── eval_sonnet.json              # Full Sonnet eval run — 88 cases, current code
└── README.md                     # This file
```

---

## DB Schema

```sql
-- Menu catalog (FTS5 virtual table mirrors this for full-text search)
menu_items (
    id TEXT PRIMARY KEY, name TEXT, category TEXT, price REAL,
    description TEXT, dietary_tags TEXT, tags TEXT,
    modifiers_json TEXT, options_json TEXT
)

-- Live inventory (quantity overrides the static available flag in menu.json)
inventory (
    item_id TEXT PRIMARY KEY REFERENCES menu_items(id),
    quantity INTEGER NOT NULL DEFAULT 0,
    low_stock_threshold INTEGER NOT NULL DEFAULT 10,
    updated_at TEXT
)

-- Inventory change log (orders and restocks)
inventory_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT, delta INTEGER, reason TEXT, order_id TEXT, logged_at TEXT
)

-- Confirmed orders (written only after payment succeeds)
orders (
    order_id TEXT PRIMARY KEY, created_at TEXT,
    subtotal REAL, delivery_fee REAL DEFAULT 0.0,
    tax REAL DEFAULT 0.0, total REAL DEFAULT 0.0,
    items_json TEXT, special_instructions TEXT,
    fulfillment_type TEXT DEFAULT 'pickup',
    email TEXT DEFAULT '', phone TEXT DEFAULT '',
    conversation_turns INTEGER
)

-- Stripe payment records (only PaymentIntent ID + status stored — no card data)
payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT, stripe_payment_id TEXT,
    amount_cents INTEGER, currency TEXT DEFAULT 'usd',
    status TEXT, fulfillment_type TEXT, created_at TEXT
)
```
