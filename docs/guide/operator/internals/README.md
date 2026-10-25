# Part I: How it works

This part gives you the mental model that every later chapter assumes: one engine, pure strategies, two interfaces, and adapters behind them.

- [Architecture](architecture.md): modules, canonical models, the two interfaces and what they leave unspecified, registries, import rules.
- [Data flow: ticks, bars and warm-up](data-flow.md): UTC buckets, how bars are built and when they are emitted, which source wins when a provider pushes bars, and how warm-up is computed.
- [Execution and risk](execution-and-risk.md): from a signal to a target position, to an order request, through the seven risk checks, to the broker and back.
- [Provider adapters](providers.md): what the paper, Alpaca and XTB adapters do, side by side, and where they differ.
