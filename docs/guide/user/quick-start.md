# Quick start

This walk-through takes about 15 minutes, most of it waiting for a broker to create your paper account. At the end you have the program printing signals for one stock on a free Alpaca paper account, with no orders sent.

## 1. Get Alpaca paper keys (5 minutes)

Create a free account at Alpaca and open the **paper trading** dashboard. Generate an API key and secret there. The program needs these keys even in signal-only mode, because Alpaca's live price stream requires them.

> **Warning.** Use the keys from the *paper* dashboard, never from a live account. For this phase of the project nothing should be connected to real money.

## 2. Install (3 minutes)

You need Python 3.11 or newer on macOS or Linux. In a terminal:

```bash
git clone https://github.com/OWNER/tradesignals.git   # or unpack the source you were given
cd tradesignals
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[alpaca,dev]"
tradesignals --version
```

The last command prints `tradesignals 0.1.0`. If it says "command not found", the virtual environment is not active: run the `source` line again.

## 3. Configure (2 minutes)

```bash
cp .env.example .env
```

Open `.env` in any text editor and fill in the two Alpaca lines:

```bash
ALPACA_API_KEY=your-paper-key
ALPACA_SECRET_KEY=your-paper-secret
ALPACA_PAPER=true
```

Leave `ALPACA_PAPER=true` exactly as it is. The rest of the file can stay as it is; the XTB lines are only needed if you use XTB.

## 4. Run signal-only (during US market hours)

```bash
tradesignals --data alpaca --symbols AAPL --strategy sma_cross
```

Run this while the US market is open (9:30 to 16:00 New York time, Monday to Friday). Outside those hours nothing arrives and the program sits quietly.

## 5. What you should see

```text
2026-10-23 15:35:02,114 INFO tradesignals.engine: warmup sma_cross/AAPL: 93 bars
2026-10-23 15:35:02,120 INFO tradesignals.alpaca: alpaca data: feed=iex symbols=['AAPL']
2026-10-23 15:35:02,121 INFO tradesignals.engine: engine running: symbols=['AAPL'] strategies=['sma_cross'] execute=False qty=1.0
2026-10-23 15:52:00,480 INFO tradesignals.engine: SIGNAL sma_cross AAPL long strength=1.00 fast=184.12 slow=183.90
```

- The `warmup` line means the program fetched recent history so the strategy is ready immediately instead of waiting half an hour for enough bars.
- `engine running` means it is now listening to live prices.
- A `SIGNAL` line appears whenever the strategy changes its mind. With the default strategy on one-minute bars that happens a few times an hour on a busy day, and sometimes not for an hour. Silence is normal. [Reading the output](reading-the-output.md) explains every line.

Stop the program with `Ctrl-C`.

## 6. Optional: let it trade a pretend account

The built-in paper broker is a pretend account inside the program: it starts with 100,000 USD, fills every order instantly at the current price, and forgets everything when you stop.

```bash
tradesignals --data alpaca --broker paper --symbols AAPL --strategy sma_cross --execute --qty 1
```

Now each `SIGNAL` is followed by an `ORDER` line (or a `RISK REJECT` line explaining why no order was sent). Nothing leaves your computer except the price subscription.

## Next steps

- [Everyday use](everyday-use.md) for choosing stocks, strategies and position sizes.
- [Trust and safety](trust-and-safety.md) before you point `--broker` at a real broker account, even a paper one.
