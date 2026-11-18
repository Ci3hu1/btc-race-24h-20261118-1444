# Washout Sniper + Fallback — btc-race-24h-20261118-1444

[![make.fun · btc-race-24h-20261118-1444](https://make.fun/badge/btc-race-24h-20261118-1444.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![rank](https://make.fun/badge/btc-race-24h-20261118-1444/Ci3hu1/btc-race-24h-20261118-1444/rank.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![reward](https://make.fun/badge/btc-race-24h-20261118-1444/Ci3hu1/btc-race-24h-20261118-1444/reward.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)

A long-only cbBTC trading bot for make.fun's "Race a BTC trading bot live for
24 hours" (btc-race-24h-20261118-1444). Python 3.13, standard library only.

## What it does

The dev window is a slow grind down (-2.80% drift), so buy-and-hold loses
about $28 and fails the dev gate (net PnL must be above $0). This bot stays
in cash by default and only buys on a confirmed washout bounce -- or, if no
such setup ever appears, on one late momentum breakout, so it always trades
at least once:

- **Sniper (at most one):** after a washout (lowest low of the last 40 bars
  at least 1.5% below the highest high of the prior 60 bars), buy when a
  thrust bar confirms the turn (closes up >= 0.20%, in the top 30% of its
  range, range >= 0.35%, above the prior 5-bar high). Full cash in.
  Exits: hard stop -0.30%, take-profit +0.80%, trailing stop 0.50% once the
  peak is +0.60% above entry, time stop 45 bars.
- **Fallback (only if no trade has filled yet, from bar 600):** buy on a
  momentum breakout (EMA10 above EMA40 and close above the prior 20-bar
  high). Exits: hard stop -0.50%, take-profit +0.80%, same trailing stop,
  time stop 60 bars.
- No orders before bar 60 and none in the last 60 bars of the window.

See `STRATEGY.md` for the full reasoning, including the honest limits.

## Layout

- `main.py` — the entrypoint the grader runs (`python3 -P -s main.py`).
  Packed by the kit's `makefun compress` from `bot.py`; do not hand-edit it.
- `bot.py` — the readable source.
- `vendor/makefun_bars.py` — byte-identical copy of the kit's
  `sdk/makefun_bars.py` (the bars-replay/v1 protocol shim).
- `tests/test_bot.py` — unit tests (EMA, washout/thrust entry, exits,
  determinism, wire protocol).

## Run the tests

```sh
python3 -m unittest discover -s tests -v
```

## Grade locally (from inside the unpacked kit)

```sh
node cli/makefun.mjs grade-local --kit . --bot <path-to-this-repo> --driver docker
```

## License

MIT — see `LICENSE`.
