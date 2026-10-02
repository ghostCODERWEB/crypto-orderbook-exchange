"""Replay collateralized limit orders, matching, cancellation and settlement with conservation checks."""

from .validation import integer, decimal, identifier
from decimal import Decimal
import copy


class Exchange:
    def __init__(self, balances):
        self.accounts = {
            identifier(a): {"base": integer(v["base"]), "quote": integer(v["quote"])}
            for a, v in balances.items()
        }
        self.orders = {}
        self.sequence = 0
        self.trades = []
        self.used_ids = set()
        self.initial = {
            asset: sum(v[asset] for v in self.accounts.values())
            for asset in ("base", "quote")
        }

    def available(self, account, asset):
        locked = sum(
            o["remaining"] * (o["price"] if asset == "quote" else 1)
            for o in self.orders.values()
            if o["owner"] == account
            and o["side"] == ("buy" if asset == "quote" else "sell")
        )
        return self.accounts[account][asset] - locked

    def submit(self, event):
        oid = identifier(event["id"])
        owner = identifier(event["owner"])
        side = event["side"]
        quantity = integer(event["quantity"], minimum=1)
        price = integer(event["price"], minimum=1)
        if (
            oid in self.used_ids
            or owner not in self.accounts
            or side not in ("buy", "sell")
        ):
            raise ValueError("invalid order identity, owner or side")
        if event.get("time_in_force", "GTC") not in ("GTC", "IOC", "FOK"):
            raise ValueError("invalid time in force")
        asset = "quote" if side == "buy" else "base"
        required = quantity * price if side == "buy" else quantity
        if self.available(owner, asset) < required:
            raise ValueError("insufficient available balance")
        candidates = [
            o
            for o in self.orders.values()
            if o["side"] != side
            and (o["price"] <= price if side == "buy" else o["price"] >= price)
        ]
        candidates.sort(
            key=lambda o: (o["price"] if side == "buy" else -o["price"], o["sequence"])
        )
        if any(o["owner"] == owner for o in candidates):
            raise ValueError("crossing self trade prohibited")
        if (
            event.get("time_in_force") == "FOK"
            and sum(o["remaining"] for o in candidates) < quantity
        ):
            return {"id": oid, "status": "unfilled_fok", "filled": 0}
        self.used_ids.add(oid)
        self.sequence += 1
        remaining = quantity
        for maker in candidates:
            if not remaining:
                break
            amount = min(remaining, maker["remaining"])
            value = amount * maker["price"]
            buyer = owner if side == "buy" else maker["owner"]
            seller = maker["owner"] if side == "buy" else owner
            self.accounts[buyer]["quote"] -= value
            self.accounts[seller]["quote"] += value
            self.accounts[buyer]["base"] += amount
            self.accounts[seller]["base"] -= amount
            maker["remaining"] -= amount
            remaining -= amount
            self.trades.append(
                {
                    "maker": maker["id"],
                    "taker": oid,
                    "quantity": amount,
                    "price": maker["price"],
                    "buyer": buyer,
                    "seller": seller,
                }
            )
            if not maker["remaining"]:
                del self.orders[maker["id"]]
        if remaining and event.get("time_in_force", "GTC") == "GTC":
            self.orders[oid] = {
                "id": oid,
                "owner": owner,
                "side": side,
                "price": price,
                "remaining": remaining,
                "sequence": self.sequence,
            }
        self.invariants()
        return {
            "id": oid,
            "filled": quantity - remaining,
            "resting": remaining if oid in self.orders else 0,
            "status": "filled" if remaining == 0 else "partial_or_open",
        }

    def cancel(self, event):
        order = self.orders.get(event["id"])
        if order is None or order["owner"] != event["owner"]:
            raise ValueError("unknown order or wrong owner")
        del self.orders[event["id"]]
        self.invariants()
        return {"cancelled": event["id"]}

    def invariants(self):
        for asset in ("base", "quote"):
            if sum(v[asset] for v in self.accounts.values()) != self.initial[asset]:
                raise ValueError("conservation violated")
            for owner in self.accounts:
                if self.available(owner, asset) < 0:
                    raise ValueError("negative free balance")

    def snapshot(self):
        book = sorted(self.orders.values(), key=lambda o: o["sequence"])
        return {
            "balances": copy.deepcopy(self.accounts),
            "orders": copy.deepcopy(book),
            "trades": copy.deepcopy(self.trades),
            "conserved_totals": self.initial,
        }


def analyze(data):
    exchange = Exchange(data["balances"])
    events = []
    for event in data["events"]:
        if event["type"] == "submit":
            events.append(exchange.submit(event))
        elif event["type"] == "cancel":
            events.append(exchange.cancel(event))
        else:
            raise ValueError("unknown event")
    return {
        **exchange.snapshot(),
        "events": events,
        "trade_count": len(exchange.trades),
    }
