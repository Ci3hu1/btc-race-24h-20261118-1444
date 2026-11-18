"""Washout Sniper + Fallback: a long-only cbBTC bot for btc-race-24h-20261118-1444.

The race: 1,440 one-minute bars of Coinbase Wrapped BTC (cbBTC) vs USDC,
$1,000 of paper money, long only, no leverage, fills at the next bar's open,
about 26 bps of fees + slippage + pool impact per round trip. The dev window
is a slow grind down (-2.80% drift, 3.35% range), so buy-and-hold loses about
$28 and the dev gate (net PnL above $0) fails it. The bot therefore stays in
cash by default and only buys on a confirmed washout bounce, or -- if no such
setup ever appears -- on one late momentum breakout, so it always trades at
least once.

Strategy (all numbers fixed; nothing here is tuned to the dev window):
  * SNIPER (at most one): after a washout -- the lowest low of the last 40
    bars sits at least 1.5% below the highest high of the prior 60 bars --
    buy when a thrust bar confirms the turn: the bar closes up at least
    0.20%, in the top 30% of its own range, with a range of at least 0.35%,
    and above the highest high of the prior 5 bars. Full cash in.
    Exits: hard stop -0.30%, take-profit +0.80%, trailing stop 0.50% once
    the peak is +0.60% above entry, time stop 45 bars.
  * FALLBACK (only if no trade has filled yet and bar >= 600): buy on a
    momentum breakout -- EMA10 above EMA40 and the close above the highest
    high of the prior 20 bars. Full cash in. Exits: hard stop -0.50%,
    take-profit +0.80%, same trailing stop, time stop 60 bars.
  * No orders before bar 60 and none in the last 60 bars of the window.

Implementation notes: standard library only, deterministic (pure float
arithmetic over the bars, no clock, no network, no files, no randomness).
The entry price is read from the harness fill in the next batch, so every
threshold is measured against the price actually paid, not the signal bar's
close. Orders carry max_slippage_bps 10000 (wide open, as the baselines do):
the modelled slippage and impact are already inside the fill.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bars

SYMBOL = "CBBTC"
PRICE_SCALE = 5
MAX_SLIPPAGE_BPS = 10_000

# --- fixed parameters (see the module docstring) ---
WARMUP_BARS = 60
LAST_ENTRY_BAR = 1380  # no new entries in the last 60 bars of a 1440-bar window
FALLBACK_FROM_BAR = 600
H60_LOOKBACK = 60
WASHOUT_LOOKBACK = 40
WASHOUT_PCT = 0.015
THRUST_MIN_RETURN = 0.002
THRUST_MIN_POSITION = 0.70
THRUST_MIN_RANGE = 0.0035
BREAKOUT_SHORT = 5
BREAKOUT_FALLBACK = 20
SNIPER_STOP = 0.003
SNIPER_TP = 0.008
SNIPER_TRAIL_ARM = 0.006
SNIPER_TRAIL = 0.005
SNIPER_TIME_STOP = 45
FALLBACK_STOP = 0.005
FALLBACK_TP = 0.008
FALLBACK_TRAIL_ARM = 0.006
FALLBACK_TRAIL = 0.005
FALLBACK_TIME_STOP = 60
MAX_TRADES = 2

SNIPER_BUY_ID = "sniper-buy"
FALLBACK_BUY_ID = "fallback-buy"
SELL_ID = "sell"


def _price(raw):
    """A price at PRICE_SCALE as dollars (float)."""
    return int(raw) / 10 ** PRICE_SCALE


class Strategy:
    """The strategy state: bar history, EMAs, and the open position."""

    def __init__(self):
        self.opens = []
        self.highs = []
        self.lows = []
        self.closes = []
        self.ema10 = None
        self.ema40 = None
        self.long = False
        self.is_sniper = False
        self.entry_price = 0.0
        self.entry_bar = -1
        self.peak = 0.0
        self.trades = 0
        self.primary_used = False

    @property
    def k(self):
        """Index of the latest bar (0-based)."""
        return len(self.closes) - 1

    def add_bar(self, bar):
        """Append one bar (string ints at price_scale) and update the EMAs."""
        o = _price(bar["o"])
        h = _price(bar["h"])
        l = _price(bar["l"])
        c = _price(bar["c"])
        self.opens.append(o)
        self.highs.append(h)
        self.lows.append(l)
        self.closes.append(c)
        if self.ema10 is None:
            self.ema10 = c
            self.ema40 = c
        else:
            self.ema10 = c * (2.0 / 11.0) + self.ema10 * (1.0 - 2.0 / 11.0)
            self.ema40 = c * (2.0 / 41.0) + self.ema40 * (1.0 - 2.0 / 41.0)

    def sync_fills(self, fills):
        """Fold this batch's fills into the position state.

        A buy fill sets the entry price from the price actually paid and
        marks the trade; a sell fill closes the position. Rejected fills
        change nothing.
        """
        for fill in fills or []:
            if fill.get("symbol") != SYMBOL or fill.get("status") != "filled":
                continue
            if fill.get("side") == "buy":
                self.long = True
                self.entry_price = _price(fill["price"])
                self.entry_bar = self.k
                self.peak = max(self.entry_price, self.highs[self.k])
                self.trades += 1
                if fill.get("client_id") == SNIPER_BUY_ID:
                    self.primary_used = True
                    self.is_sniper = True
                elif fill.get("client_id") == FALLBACK_BUY_ID:
                    self.is_sniper = False
            elif fill.get("side") == "sell":
                self.long = False
                self.is_sniper = False

    def _sniper_entry(self, k):
        if self.primary_used or self.trades >= MAX_TRADES:
            return False
        h60 = max(self.highs[k - H60_LOOKBACK:k])
        washout = min(self.lows[k - WASHOUT_LOOKBACK:k + 1]) <= h60 * (1.0 - WASHOUT_PCT)
        if not washout:
            return False
        o = self.opens[k]
        h = self.highs[k]
        l = self.lows[k]
        c = self.closes[k]
        rng = h - l
        if rng <= 0:
            return False
        return (
            c / o - 1.0 >= THRUST_MIN_RETURN
            and c > o
            and (c - l) / rng >= THRUST_MIN_POSITION
            and rng / c >= THRUST_MIN_RANGE
            and c > max(self.highs[k - BREAKOUT_SHORT:k])
        )

    def _fallback_entry(self, k):
        return (
            self.ema10 > self.ema40
            and self.closes[k] > max(self.highs[k - BREAKOUT_FALLBACK:k])
        )

    def _buy(self, state, client_id):
        notional = state.cash
        min_notional = int(state.hello["fees"]["min_notional_quote_units"])
        if notional < min_notional:
            return None
        return {
            "side": "buy",
            "symbol": SYMBOL,
            "notional_quote_units": notional,
            "max_slippage_bps": MAX_SLIPPAGE_BPS,
            "client_id": client_id,
        }

    def decide(self, state):
        """The orders for this batch (possibly none)."""
        k = self.k
        if k < 0:
            return []
        if self.long:
            holding = state.positions.get(SYMBOL)
            if holding is None or holding.qty <= 0:
                return []
            self.peak = max(self.peak, self.highs[k])
            bars_held = k - self.entry_bar
            close = self.closes[k]
            if self.is_sniper:
                stop, tp = SNIPER_STOP, SNIPER_TP
                arm, trail = SNIPER_TRAIL_ARM, SNIPER_TRAIL
                time_stop = SNIPER_TIME_STOP
            else:
                stop, tp = FALLBACK_STOP, FALLBACK_TP
                arm, trail = FALLBACK_TRAIL_ARM, FALLBACK_TRAIL
                time_stop = FALLBACK_TIME_STOP
            exit_now = (
                close <= self.entry_price * (1.0 - stop)
                or close >= self.entry_price * (1.0 + tp)
                or (self.peak >= self.entry_price * (1.0 + arm)
                    and close <= self.peak * (1.0 - trail))
                or bars_held >= time_stop
            )
            if exit_now:
                return [{
                    "side": "sell",
                    "symbol": SYMBOL,
                    "qty": holding.qty,
                    "max_slippage_bps": MAX_SLIPPAGE_BPS,
                    "client_id": SELL_ID,
                }]
            return []
        # Flat: entries only inside the tradable window.
        if state.warming_up or k < WARMUP_BARS or k >= LAST_ENTRY_BAR:
            return []
        if self._sniper_entry(k):
            order = self._buy(state, SNIPER_BUY_ID)
            return [order] if order is not None else []
        if self.trades == 0 and k >= FALLBACK_FROM_BAR and self._fallback_entry(k):
            order = self._buy(state, FALLBACK_BUY_ID)
            return [order] if order is not None else []
        return []


STRATEGY = Strategy()


def on_batch(state, batch):
    """One batch in, the orders decided on it out."""
    bar = None
    for b in batch.get("bars", []):
        if b.get("symbol") == SYMBOL:
            bar = b
            break
    if bar is not None:
        STRATEGY.add_bar(bar)
    STRATEGY.sync_fills(batch.get("fills", []))
    return STRATEGY.decide(state)


if __name__ == "__main__":
    makefun_bars.run(on_batch)
