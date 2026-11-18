"""Unit tests for bot.py (Washout Sniper + Fallback).

Stdlib only: run with `python3 -m unittest discover -s tests -v` from the
repository root.
"""

import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor"))

import bot
import makefun_bars

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bar(o, h, l, c):
    """One bar from dollar floats (converted to price_scale-5 string ints)."""
    return {
        "o": str(int(round(o * 1e5))),
        "h": str(int(round(h * 1e5))),
        "l": str(int(round(l * 1e5))),
        "c": str(int(round(c * 1e5))),
    }


class FakeState:
    """Minimal stand-in for makefun_bars.BarsState for decide() tests."""

    def __init__(self, cash=1_000_000_000, positions=None, warming_up=False):
        self.cash = cash
        self.positions = positions or {}
        self._warming_up = warming_up
        self.hello = {"fees": {"min_notional_quote_units": "1000000"}}

    @property
    def warming_up(self):
        return self._warming_up


def feed(strategy, bars):
    """Feed bars to a Strategy with no fills; returns the decisions per bar."""
    state = FakeState()
    out = []
    for b in bars:
        strategy.add_bar(b)
        strategy.sync_fills([])
        out.append(strategy.decide(state))
    return out


class TestEma(unittest.TestCase):
    def test_ema_seeded_with_first_close(self):
        s = bot.Strategy()
        s.add_bar(bar(100, 100, 100, 100))
        self.assertEqual(s.ema10, 100.0)
        self.assertEqual(s.ema40, 100.0)
        s.add_bar(bar(102, 102, 102, 102))
        self.assertAlmostEqual(s.ema10, 1104 / 11, places=9)
        s.add_bar(bar(101, 101, 101, 101))
        self.assertAlmostEqual(s.ema10, 12158 / 121, places=9)

    def test_ema40_moves_slower_than_ema10(self):
        s = bot.Strategy()
        for c in [100, 110, 120, 130, 140]:
            s.add_bar(bar(c, c, c, c))
        self.assertGreater(s.ema10, s.ema40)


class TestSniperEntry(unittest.TestCase):
    def _washout_bars(self, thrust=True):
        """70 flat bars at 65000, a washout dip, then a thrust bar at k=71."""
        bars = [bar(65000, 65000, 65000, 65000) for _ in range(70)]
        # Bar 70: washout -- low 1.6% below the 60-bar high (65000 -> 63960).
        bars.append(bar(64800, 64800, 63960, 64100))
        if thrust:
            # Bar 71: thrust -- up 1.53%, close in the top of its range,
            # range 1.54%, above the prior 5-bar high (65000).
            bars.append(bar(64100, 65100, 64100, 65080))
        else:
            # Bar 71: weak bar -- closes in the bottom of its range.
            bars.append(bar(64100, 65100, 64100, 64300))
        return bars

    def test_sniper_fires_on_washout_plus_thrust(self):
        s = bot.Strategy()
        decisions = feed(s, self._washout_bars(thrust=True))
        self.assertEqual(decisions[:71], [[]] * 71)
        self.assertEqual(len(decisions[71]), 1)
        order = decisions[71][0]
        self.assertEqual(order["side"], "buy")
        self.assertEqual(order["client_id"], "sniper-buy")
        self.assertEqual(order["notional_quote_units"], 1_000_000_000)

    def test_sniper_skips_weak_thrust_bar(self):
        s = bot.Strategy()
        decisions = feed(s, self._washout_bars(thrust=False))
        self.assertEqual(decisions, [[]] * len(decisions))

    def test_no_entry_before_bar_60(self):
        s = bot.Strategy()
        bars = [bar(65000, 65000, 65000, 65000) for _ in range(58)]
        bars.append(bar(64800, 64800, 63960, 64100))
        bars.append(bar(64100, 65100, 64100, 65080))
        decisions = feed(s, bars)
        self.assertEqual(decisions, [[]] * len(decisions))

    def test_no_entry_in_last_60_bars(self):
        s = bot.Strategy()
        bars = [bar(65000, 65000, 65000, 65000) for _ in range(1380)]
        bars.append(bar(64800, 64800, 63960, 64100))
        bars.append(bar(64100, 65100, 64100, 65080))
        decisions = feed(s, bars)
        self.assertEqual(decisions, [[]] * len(decisions))


class TestFallbackEntry(unittest.TestCase):
    def test_fallback_fires_after_bar_600_on_breakout(self):
        s = bot.Strategy()
        # 600 bars drifting slowly up so EMA10 > EMA40, then a breakout bar.
        bars = []
        price = 64000.0
        for _ in range(600):
            bars.append(bar(price, price, price, price))
            price += 1.0
        bars.append(bar(price, price + 50, price, price + 50))
        decisions = feed(s, bars)
        buys = [(i, d) for i, d in enumerate(decisions) if d]
        self.assertEqual(len(buys), 1)
        self.assertEqual(buys[0][0], 600)
        self.assertEqual(buys[0][1][0]["client_id"], "fallback-buy")

    def test_fallback_never_before_bar_600(self):
        s = bot.Strategy()
        bars = []
        price = 64000.0
        for _ in range(300):
            bars.append(bar(price, price, price, price))
            price += 1.0
        bars.append(bar(price, price + 50, price, price + 50))
        decisions = feed(s, bars)
        self.assertEqual(decisions, [[]] * len(decisions))


class TestExits(unittest.TestCase):
    def _long_strategy(self, sniper=True, entry=64000.0):
        s = bot.Strategy()
        for _ in range(70):
            s.add_bar(bar(65000, 65000, 65000, 65000))
        s.add_bar(bar(64000, 64350, 64000, 64256))
        fill = {
            "symbol": "CBBTC",
            "side": "buy",
            "status": "filled",
            "client_id": "sniper-buy" if sniper else "fallback-buy",
            "price": str(int(round(entry * 1e5))),
            "qty": "15600000",
            "decided_bar": 69,
            "fill_bar": 70,
        }
        s.sync_fills([fill])
        return s

    def _state_long(self, qty=15600000):
        return FakeState(
            cash=1000,
            positions={"CBBTC": makefun_bars.Holding(qty, 999000000)},
        )

    def test_take_profit(self):
        s = self._long_strategy()
        state = self._state_long()
        # +0.90% close triggers the +0.80% take-profit.
        s.add_bar(bar(64576, 64600, 64500, 64576))
        s.sync_fills([])
        orders = s.decide(state)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["side"], "sell")
        self.assertEqual(orders[0]["qty"], 15600000)

    def test_hard_stop(self):
        s = self._long_strategy()
        state = self._state_long()
        # -0.40% close triggers the -0.30% sniper stop.
        s.add_bar(bar(63744, 63800, 63700, 63744))
        s.sync_fills([])
        orders = s.decide(state)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["side"], "sell")

    def test_trailing_stop_after_arm(self):
        s = self._long_strategy()
        state = self._state_long()
        # Run up +0.70% (arms the trailing stop), then fall 0.60% off the peak.
        s.add_bar(bar(64448, 64480, 64400, 64448))
        s.sync_fills([])
        self.assertEqual(s.decide(state), [])
        s.add_bar(bar(64080, 64100, 64050, 64080))
        s.sync_fills([])
        orders = s.decide(state)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["side"], "sell")

    def test_no_trailing_before_arm(self):
        s = self._long_strategy()
        state = self._state_long()
        # Small +0.20% peak, then a -0.40% close: no arm, no stop, no TP.
        s.add_bar(bar(64128, 64150, 64100, 64128))
        s.sync_fills([])
        self.assertEqual(s.decide(state), [])
        s.add_bar(bar(63872, 63900, 63850, 63872))
        s.sync_fills([])
        self.assertEqual(s.decide(state), [])

    def test_time_stop(self):
        s = self._long_strategy()
        state = self._state_long()
        # 45 flat bars at entry: nothing triggers until bars_held hits 45.
        for i in range(44):
            s.add_bar(bar(64000, 64000, 64000, 64000))
            s.sync_fills([])
            self.assertEqual(s.decide(state), [], f"bar {i}")
        s.add_bar(bar(64000, 64000, 64000, 64000))
        s.sync_fills([])
        orders = s.decide(state)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["side"], "sell")

    def test_fallback_time_stop_is_60(self):
        s = self._long_strategy(sniper=False)
        state = self._state_long()
        for _ in range(59):
            s.add_bar(bar(64000, 64000, 64000, 64000))
            s.sync_fills([])
            self.assertEqual(s.decide(state), [])
        s.add_bar(bar(64000, 64000, 64000, 64000))
        s.sync_fills([])
        self.assertEqual(len(s.decide(state)), 1)


class TestDeterminism(unittest.TestCase):
    def test_same_bars_same_orders_twice(self):
        bars = [bar(65000, 65000, 65000, 65000) for _ in range(70)]
        bars.append(bar(64800, 64800, 63960, 64100))
        bars.append(bar(64100, 65100, 64100, 65080))
        for i in range(20):
            bars.append(bar(65080 + i * 10, 65120 + i * 10, 65050 + i * 10, 65080 + i * 10))
        runs = []
        for _ in range(2):
            s = bot.Strategy()
            state = FakeState()
            run_orders = []
            for b in bars:
                s.add_bar(b)
                s.sync_fills([])
                run_orders.append(s.decide(state))
            runs.append(run_orders)
        self.assertEqual(runs[0], runs[1])


class TestWire(unittest.TestCase):
    """The SDK shim: on_batch through BarsState answers valid replies."""

    def test_run_over_synthetic_batches(self):
        bot.STRATEGY = bot.Strategy()
        hello = {
            "type": "hello",
            "protocol": "agent-fun/bars-replay/v1",
            "seed": "1",
            "symbols": [{"symbol": "CBBTC", "mint": "cbbtcf3aa214zXHbiAZQwf4122FBYbraNdFqgw4iMij",
                         "price_scale": 5, "qty_scale": 8}],
            "capital": {"quote": "USDC", "quote_scale": 6, "quote_units": "1000000000"},
            "bar_interval": "1m",
            "first_close_ms": 0,
            "caps": {"max_open_positions": 1, "max_orders_per_bar": 16,
                     "max_weight_bps": 10000, "shorts": False, "leverage": False},
            "fees": {"fee_bps": 4, "slippage_bps": 2, "min_notional_quote_units": "1000000"},
            "latency_bars": 1,
            "warmup_bars": 0,
        }
        lines = [json.dumps(hello)]
        for seq in range(3):
            batch = {
                "type": "batch", "seq": seq, "t": seq * 60000,
                "bars": [{"symbol": "CBBTC", "t": seq * 60000,
                          "o": "6500000000", "h": "6500000000",
                          "l": "6500000000", "c": "6500000000",
                          "o_depth": ["1000000000000"], "c_depth": ["1000000000000"]}],
                "fills": [],
                "account": {"cash_quote_units": "1000000000", "positions": [],
                            "equity_quote_units": "1000000000"},
            }
            lines.append(json.dumps(batch))
        lines.append(json.dumps({
            "type": "done", "seq": 3,
            "account": {"cash_quote_units": "1000000000", "positions": [],
                        "equity_quote_units": "1000000000"},
        }))
        data = ("\n".join(lines) + "\n").encode("utf-8")
        out = io.StringIO()
        result = makefun_bars.run(bot.on_batch, input=io.BytesIO(data), output=out)
        replies = [json.loads(line) for line in out.getvalue().strip().split("\n")]
        self.assertEqual(len(replies), 3)
        self.assertEqual([r["seq"] for r in replies], [0, 1, 2])
        self.assertTrue(all(r["orders"] == [] for r in replies))
        self.assertEqual(result.batches, 3)


if __name__ == "__main__":
    unittest.main()
