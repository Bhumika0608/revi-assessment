#!/usr/bin/env python3
# Run: python3 demo.py
"""
Interactive CLI demo for the Talkin' Tacos ordering agent.

Usage:
  python demo.py
  CLAUDE_MODEL=claude-haiku-4-5-20251001 python demo.py   # faster / cheaper
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from agent.agent import MODEL, take_order
from db.logging_config import setup_logging
from db.restaurant import NAME as RESTAURANT_NAME
from db.setup import init_db

setup_logging()


def _divider(char="─", width=60):
    print(char * width)


def main():
    print()
    _divider("═")
    print(f"  🌮  {RESTAURANT_NAME.upper()} — Order Agent Demo")
    print(f"  Model: {MODEL}")
    _divider("═")
    print("  Type your order. 'quit' to exit, 'new' to start a fresh order.\n")

    init_db()

    history: list[dict] = []
    turn = 0

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue

        if user_input.lower() in ("quit", "exit", "q"):
            print("Goodbye! Come back for more tacos.")
            break

        if user_input.lower() in ("new", "restart", "reset"):
            history = []
            turn = 0
            _divider()
            print("  New order started.\n")
            continue

        t0 = time.perf_counter()
        try:
            response = take_order(user_input, history if history else None)
        except Exception as exc:
            print(f"\n[ERROR] {exc}\n", file=sys.stderr)
            continue
        elapsed_ms = (time.perf_counter() - t0) * 1000

        turn += 1
        status = response["status"]
        agent_msg = response["agent_message"]

        print(f"\nAgent [{status} · {elapsed_ms:.0f}ms]: {agent_msg}\n")

        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": agent_msg})

        if status == "confirmed":
            order = response["order"]
            _divider("═")
            print(f"  ORDER CONFIRMED — #{order['order_id']}")
            _divider()
            for item in order["items"]:
                mods = f"  [{', '.join(item['modifiers'])}]" if item.get("modifiers") else ""
                print(f"  {item['quantity']}x  {item['name']:<30} ${item['line_total']:.2f}{mods}")
            _divider()
            print(f"  Subtotal: ${order['subtotal']:.2f}")
            if order.get("special_instructions"):
                print(f"  Note: {order['special_instructions']}")
            _divider("═")
            print()

            ans = input("Start a new order? (y/n): ").strip().lower()
            if ans == "y":
                history = []
                turn = 0
                print()
            else:
                print(f"Thanks for visiting {RESTAURANT_NAME}!")
                break

        elif status == "refused":
            _divider()
            print("  [Request declined — type something else or 'quit']\n")


if __name__ == "__main__":
    main()
