"""
Inventory management page for Talkin' Tacos.

Run alongside the main app: streamlit run ui/app.py
Navigate via the sidebar.

Access control: gated behind ADMIN_PASSWORD env var. If the env var isn't set,
the page is hard-locked (not open by default). Once unlocked in a session,
the user stays unlocked until they click "Lock" or reload the tab.

Layout:
  - KPI tiles at top (total / in stock / low / out)
  - Three tabs: Restock (primary action) · Browse Stock · Recent Changes
  - Stock browsing uses st.dataframe — sortable, searchable, scales to the 10k catalog
"""

import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from db.admin_auth import is_admin_configured, verify_admin_password
from db.restaurant import NAME as RESTAURANT_NAME
from db.setup import get_inventory, get_inventory_log, init_db, restock_item

st.set_page_config(
    page_title=f"Inventory — {RESTAURANT_NAME}",
    page_icon="📦",
    layout="wide",
)

init_db()


# ── Auth gate ─────────────────────────────────────────────────────────────────

st.title("📦 Inventory")

if not is_admin_configured():
    st.error(
        "Inventory admin is disabled — `ADMIN_PASSWORD` is not set on the server. "
        "Add it to `.env` to enable this page."
    )
    st.stop()

if "admin_authenticated" not in st.session_state:
    st.session_state.admin_authenticated = False

if not st.session_state.admin_authenticated:
    st.warning("This page is restricted. Enter the admin password to continue.")
    with st.form("admin_login", clear_on_submit=True):
        submitted_pw = st.text_input("Admin password", type="password")
        login = st.form_submit_button("Unlock", use_container_width=True)
    if login:
        if verify_admin_password(submitted_pw):
            st.session_state.admin_authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()


# ── Header row: title bar with lock + refresh on the right ───────────────────

header_left, _, refresh_col, lock_col = st.columns([6, 4, 1, 1])
with header_left:
    st.caption("Live stock — decremented on every confirmed order, manually restock-able below.")
with refresh_col:
    if st.button("🔄", help="Refresh inventory data", use_container_width=True):
        st.rerun()
with lock_col:
    if st.button("🔒", help="Lock this session", use_container_width=True):
        st.session_state.admin_authenticated = False
        st.rerun()


# ── Load data once per render ─────────────────────────────────────────────────

inventory = get_inventory()
total_items  = len(inventory)
out_of_stock = sum(1 for i in inventory if i["quantity"] == 0)
low_stock    = sum(1 for i in inventory if 0 < i["quantity"] <= i["low_stock_threshold"])
in_stock     = total_items - out_of_stock - low_stock


# ── KPI tiles ─────────────────────────────────────────────────────────────────

k1, k2, k3, k4 = st.columns(4)
k1.metric("Total SKUs",   f"{total_items:,}")
k2.metric("In Stock",     f"{in_stock:,}")
k3.metric("Low Stock",    f"{low_stock:,}",
          delta=f"-{low_stock}" if low_stock else None,
          delta_color="inverse" if low_stock else "off")
k4.metric("Out of Stock", f"{out_of_stock:,}",
          delta=f"-{out_of_stock}" if out_of_stock else None,
          delta_color="inverse" if out_of_stock else "off")

st.markdown("")  # tighter spacer than divider


# ── Tabs: Restock · Browse Stock · Recent Changes ────────────────────────────

restock_tab, browse_tab, log_tab = st.tabs([
    f"🔄 Restock",
    f"📊 Browse Stock  ({total_items:,})",
    "📜 Recent Changes",
])


# ── Tab 1: Restock ────────────────────────────────────────────────────────────

with restock_tab:
    form_col, alerts_col = st.columns([1, 1])

    with form_col:
        st.markdown("##### Add stock to an item")

        # Optional category filter narrows the selectbox — at 10k items, scrolling
        # the full list is painful even with type-to-search.
        all_categories = ["All categories"] + sorted({i["category"] for i in inventory})
        sel_category   = st.selectbox(
            "Filter by category",
            all_categories,
            key="restock_category_filter",
        )
        candidates = (
            inventory if sel_category == "All categories"
            else [i for i in inventory if i["category"] == sel_category]
        )

        # Sort low stock first so they bubble up to the top of the dropdown
        candidates = sorted(candidates, key=lambda i: (i["quantity"], i["name"]))

        item_labels = {
            f"{i['name']} · {i['category']} · {i['quantity']} units left": i["id"]
            for i in candidates
        }

        with st.form("restock_form", clear_on_submit=True):
            selected_label = st.selectbox(
                f"Select item ({len(candidates):,} in scope)",
                list(item_labels.keys()),
                help="Items are sorted with the lowest stock first.",
            )
            add_qty = st.number_input(
                "Units to add",
                min_value=1, max_value=500, value=50, step=10,
            )
            submitted = st.form_submit_button(
                f"🔄 Restock", use_container_width=True, type="primary",
            )

        if submitted and selected_label:
            item_id = item_labels[selected_label]
            restock_item(item_id, int(add_qty))
            item_name = selected_label.split(" · ", 1)[0]
            st.success(f"✓ Added {add_qty} units to **{item_name}**.")
            st.rerun()

    with alerts_col:
        st.markdown("##### Needs attention")
        critical = [i for i in inventory if i["quantity"] <= i["low_stock_threshold"]]
        critical.sort(key=lambda i: i["quantity"])
        if not critical:
            st.success("All items are above their low-stock threshold. 🟢")
        else:
            shown = critical[:8]
            for it in shown:
                status_icon = "🔴" if it["quantity"] == 0 else "🟡"
                status_text = "Out of stock" if it["quantity"] == 0 else f"{it['quantity']} left"
                st.markdown(
                    f"{status_icon} **{it['name']}**  \n"
                    f"<small style='color:#888'>{it['category']} · "
                    f"{status_text} · threshold {it['low_stock_threshold']}</small>",
                    unsafe_allow_html=True,
                )
            if len(critical) > len(shown):
                st.caption(f"+{len(critical) - len(shown):,} more — use the **Browse Stock** tab to see all.")


# ── Tab 2: Browse Stock — single sortable table over the full catalog ────────

with browse_tab:
    filter_col1, filter_col2, filter_col3 = st.columns([2, 1, 1])

    with filter_col1:
        name_query = st.text_input(
            "Search by name",
            placeholder="e.g. birria, pollo, taco…",
            label_visibility="visible",
        )
    with filter_col2:
        cat_filter = st.selectbox(
            "Category",
            ["All"] + sorted({i["category"] for i in inventory}),
        )
    with filter_col3:
        status_filter = st.selectbox(
            "Status",
            ["All", "In stock", "Low stock", "Out of stock"],
        )

    def _status(qty: int, threshold: int) -> str:
        if qty == 0:
            return "🔴 Out of stock"
        if qty <= threshold:
            return "🟡 Low stock"
        return "🟢 In stock"

    rows = []
    for it in inventory:
        s = _status(it["quantity"], it["low_stock_threshold"])
        if name_query and name_query.lower() not in it["name"].lower():
            continue
        if cat_filter != "All" and it["category"] != cat_filter:
            continue
        if status_filter != "All" and not s.endswith(status_filter):
            continue
        rows.append({
            "Status":    s,
            "Name":      it["name"],
            "Category":  it["category"].title(),
            "Quantity":  it["quantity"],
            "Threshold": it["low_stock_threshold"],
            "Price":     f"${it['price']:.2f}",
        })

    df = pd.DataFrame(rows)

    st.caption(f"Showing {len(df):,} of {total_items:,} items.")
    if df.empty:
        st.info("No items match the current filters.")
    else:
        st.dataframe(
            df,
            use_container_width=True,
            hide_index=True,
            height=min(600, 35 + 36 * len(df)),
            column_config={
                "Status":    st.column_config.TextColumn(width="small"),
                "Name":      st.column_config.TextColumn(width="medium"),
                "Category":  st.column_config.TextColumn(width="small"),
                "Quantity":  st.column_config.NumberColumn(format="%d", width="small"),
                "Threshold": st.column_config.NumberColumn(format="%d", width="small"),
                "Price":     st.column_config.TextColumn(width="small"),
            },
        )


# ── Tab 3: Recent Changes ─────────────────────────────────────────────────────

with log_tab:
    log = get_inventory_log(limit=80)
    if not log:
        st.info("No inventory changes recorded yet. Decrements happen automatically when an order is placed; restocks happen from the **Restock** tab above.")
    else:
        log_filter = st.radio(
            "Show",
            ["All", "Orders only", "Restocks only"],
            horizontal=True,
            label_visibility="collapsed",
        )

        log_rows = []
        for entry in log:
            reason = entry["reason"]
            if log_filter == "Orders only"   and reason != "order":   continue
            if log_filter == "Restocks only" and reason == "order":   continue
            delta = entry["delta"]
            log_rows.append({
                "When":     entry["logged_at"],
                "Item":     entry["item_name"],
                "Δ":        f"{'+' if delta > 0 else ''}{delta}",
                "Reason":   reason,
                "Order":    entry["order_id"] or "—",
            })

        df_log = pd.DataFrame(log_rows)
        if df_log.empty:
            st.caption("No matches for the selected filter.")
        else:
            st.caption(f"Last {len(df_log)} change(s).")
            st.dataframe(
                df_log,
                use_container_width=True,
                hide_index=True,
                height=min(500, 35 + 36 * len(df_log)),
                column_config={
                    "When":   st.column_config.TextColumn(width="medium"),
                    "Item":   st.column_config.TextColumn(width="medium"),
                    "Δ":      st.column_config.TextColumn(width="small"),
                    "Reason": st.column_config.TextColumn(width="small"),
                    "Order":  st.column_config.TextColumn(width="small"),
                },
            )
