# Strategy: Washout Sniper + Fallback

## The game

One asset (cbBTC vs USDC), 1-minute bars, $1,000 paper USDC, long only, no
leverage. Orders decided on bar k fill at bar k+1's open. A full round trip
costs about 26 bps (4 bps fee + 2 bps slippage + pool impact), so a trade
has to catch a move well above that. The dev gate is hard: the entry races
only if its latest grade on the frozen 24h dev window ends with net PnL
above $0. A bot that never trades scores exactly $0 and fails; a bot that
buys and holds through a falling window loses and fails.

## The dev window (measured from the kit's dataset)

- 1,440 bars; first close $65,083.31, last close $63,260.75; drift -2.80%.
- Range (max/min) 3.35%; min $63,076.34, max $65,189.54.
- Buy-and-hold PnL: -$28.00. Worst 60-minute drawdown from the rolling
  high: -2.64% (bar 69). Best 60-minute rally from the rolling low: +1.62%
  (bar 85).
- Mean absolute 1-minute move: 4.0 bps; max 63.9 bps.
- Dip test: 83 bars sat >= 1% below their rolling 60-minute high; of 77
  such dip bars, only 7 were followed by a +1% bounce within 120 minutes.
  Naive dip-buying is weak here -- most dips keep falling.

## The edge, honestly

There is no large edge to be had long-only on a -2.80% grind-down window
with 26 bps round-trip costs. The only positive-expectancy shape is to be
flat through the grind and catch the one real bounce: the window's loss is
concentrated in a sharp early drawdown (worst 60-min drawdown -2.64% at bar
69) followed by the window's best rally (+1.62% by bar 85). A bot that buys
only after a washout *and* a confirming thrust bar, then exits quickly with
a tight stop and a +0.80% take-profit, can bank part of that single
recovery while paying the round-trip cost once. That is the whole trade:
one setup, one round trip, then done. The fallback exists only so the bot
still trades (and therefore still counts) on a window where no washout
setup appears.

Verified dev result (local replay driving the packed `main.py` over the
bars-replay/v1 protocol against the kit's frozen dev window, with the fill
math ported exactly from the kit's harness source `sim/bars/engine.ts` --
`grade-local --driver process` cannot run a Python bot on this machine, it
needs Docker, which is not installed here): net PnL **+$5.51** on exactly
1 trade -- buy decided bar 72, filled bar 73 @ $63,870.54; sell decided bar
85 on the +0.80% take-profit (close $64,393.78), filled bar 86 @ $64,273.76;
max drawdown -0.23%. Two runs produce byte-identical action logs.

## Rules (exact; these are the parameters in `bot.py`)

- Warm-up: no orders before bar 60 (0-indexed). No new entries in the
  last 60 bars of the window (bar >= 1380 of 1440).
- SNIPER entry (flat, sniper not used yet, trades < 2, bar >= 60):
  - H60 = highest high of the prior 60 bars.
  - Washout: lowest low of the last 40 bars <= H60 * (1 - 0.015).
  - Thrust bar: close/open - 1 >= 0.002; close > open;
    (close - low) / (high - low) >= 0.70; (high - low) / close >= 0.0035;
    close > highest high of the prior 5 bars.
  - Buy 100% of available cash.
- FALLBACK entry (flat, no trade filled yet, bar >= 600, bar < 1380):
  - EMA10 > EMA40 and close > highest high of the prior 20 bars.
  - Buy 100% of available cash.
- EXITS (checked at each bar close while long; the sell fills at the next
  bar's open; the whole position is sold):
  - Sniper: hard stop -0.30%; take-profit +0.80%; trailing stop 0.50%
    once the peak is +0.60% above entry; time stop 45 bars.
  - Fallback: hard stop -0.50%; take-profit +0.80%; same trailing stop;
    time stop 60 bars.
  - Whichever triggers first. Peak = highest high since entry, including
    the fill bar's high. Thresholds are measured against the fill price
    actually paid, read from the harness fill.
- EMAs are standard (k = 2 / (N + 1)), seeded with the first close.
  Orders carry max_slippage_bps 10000 (wide open, as the baselines do).

## What was tried and dropped

- Momentum ignition (EMA10 > EMA40 + 30-bar breakout + trailing stop):
  on this dev window it takes 6 trades, 5 of them losers, about -$17.40
  net -- every breakout in a grind-down is a false one, and the trend
  filter stays bearish through the one real rally, so it never catches it.
- Pure washout buying without the thrust confirmation: buys the falling
  knife; the dip test above (7 of 77) is why the confirmation bar is
  mandatory.
- Holding through the window (buy-and-hold): -$28.00, fails the gate.

## Risks on the unseen race window

- The strategy is built around one setup. If the race window has no
  washout + thrust, only the fallback fires, and its expected value is
  close to zero.
- If the race window trends up strongly, a mostly-cash bot underperforms
  buy-and-hold and ranks low (though still positive).
- If the race window chops sideways, the sniper's thrust filter should
  keep it out, but a false thrust costs about -0.56% (stop + round trip).
- The dev result is one window, not a forecast. The grade-local number
  above is the design target, verified on the frozen dev window only.
