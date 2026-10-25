# Strategies explained

This page describes the two built-in strategies in words: what they watch, when they say LONG or FLAT, and how much history they need before they say anything. Their numbers (lengths, thresholds) are fixed; only the bar size can be changed from the command line with `--timeframe`.

## sma_cross (the default)

**Idea.** Two moving averages of the closing price: a fast one over the last 10 bars and a slow one over the last 30. When the fast average rises above the slow one, the trend has turned up: **LONG**. When it falls back below: **FLAT**. Equal counts as "below".

**Bar size.** 1-minute bars by default, so with the defaults it reacts within minutes and can flip several times an hour on a choppy day. On `--timeframe 1h` it reacts within hours and flips rarely.

**History needed.** 31 bars. The program fetches up to 93 bars of history at startup (three times the need, to allow for gaps), so on a normal trading day the strategy is ready at once. Right after the open, or on Monday morning with 1-minute bars, there may not be 31 bars of today yet; the program then says `strategy starts cold` and waits.

**First signal.** The strategy only speaks when the averages *cross*. If the fast average is already above the slow one when you start, you get nothing until it crosses back down, so the first signal of a run is often FLAT. On the pretend paper broker that first FLAT is a `no-op` because you hold nothing.

**Strength.** Always 1.0, so the target position is exactly `--qty` shares.

## rsi_reversion

**Idea.** The Relative Strength Index (RSI) over 14 bars measures how one-sided recent moves have been, from 0 (every bar fell) to 100 (every bar rose). The strategy bets on a bounce: when RSI drops below 30 the stock is "oversold": **LONG**. When RSI climbs back above 55: **FLAT**.

**Bar size.** 5-minute bars by default.

**History needed.** 42 bars, which is three and a half hours of 5-minute bars. The program fetches up to ten and a half hours of history. Early in the day the history includes yesterday's bars, which is fine for this strategy.

**Strength.** Between 0.5 and 1.0: the deeper the oversold reading, the closer to 1.0. The target position is `--qty` times the strength, so with `--qty 2` a mild signal targets 1 share and a strong one 2 shares, and in between you get fractions such as 1.5. If your broker does not support fractional shares, use `--qty 1`, which gives targets between 0.5 and 1 and will still be fractional; the pretend paper broker accepts any quantity.

**Quirk.** If the price does not move at all for 14 bars, RSI reads 100 and the strategy says FLAT. That only matters on very quiet instruments.

## What both strategies share

- They look at closing prices of complete bars. A bar is complete when the next one starts, so a signal appears a moment after a bar boundary, never in the middle of a bar.
- They never say "short". Positions are either a long position of `--qty` shares (times strength) or nothing.
- They have no stop-loss or take-profit. The exit is the opposite signal, or you closing the position yourself.
- The number of signals per day depends entirely on the bar size and how choppy the stock is. More signals is not better; each one on an executing account is an order with a spread cost.
