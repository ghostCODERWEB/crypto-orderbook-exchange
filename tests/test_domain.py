import unittest, copy, json
from decimal import Decimal
from pathlib import Path
from cryptolab import analyze
from cryptolab.engine import *

EXAMPLE = json.loads(Path("examples/input.json").read_text())


class DomainTests(unittest.TestCase):
    def test_maker_price(self):
        r = analyze(EXAMPLE)
        self.assertEqual(r["trades"][0]["price"], 100)
        self.assertEqual(r["balances"]["bob"], {"base": 30, "quote": 7000})

    def test_fifo(self):
        d = copy.deepcopy(EXAMPLE)
        d["events"].insert(1, {**d["events"][0], "id": "ask-2", "quantity": 10})
        d["events"][-1]["quantity"] = 55
        self.assertEqual([x["maker"] for x in analyze(d)["trades"]], ["ask-1", "ask-2"])

    def test_locked_collateral(self):
        d = copy.deepcopy(EXAMPLE)
        d["events"] = [d["events"][0], {**d["events"][0], "id": "more", "quantity": 60}]
        with self.assertRaises(ValueError):
            analyze(d)

    def test_cancel_authorization(self):
        d = copy.deepcopy(EXAMPLE)
        d["events"].append({"type": "cancel", "id": "ask-1", "owner": "bob"})
        with self.assertRaises(ValueError):
            analyze(d)

    def test_fok_no_settlement(self):
        d = copy.deepcopy(EXAMPLE)
        d["events"][-1].update(quantity=60, time_in_force="FOK")
        r = analyze(d)
        self.assertEqual(r["trade_count"], 0)
        self.assertEqual(r["events"][-1]["status"], "unfilled_fok")
