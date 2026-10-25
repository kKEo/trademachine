# Operator guide

This guide is for people who run, configure or change the tradesignals engine: operators who keep it running on a paper or demo account, and developers who add a strategy or a provider. It assumes you are comfortable with Python, a terminal and the idea of an asyncio event loop. It quotes defaults, file paths and line numbers so that you can check every claim against the code.

| Part | Read it when you need to... |
|---|---|
| [Part I: How it works](internals/README.md) | understand the pipeline before touching a setting: architecture, tick-to-bar aggregation, warm-up, execution, risk checks, and how each provider adapter behaves |
| [Part II: Install and run](install/README.md) | install, look up any environment variable or command-line flag with its default, and set up Alpaca or XTB accounts, feeds and symbols |
| [Part III: Operate](operate/README.md) | watch a running engine, know what each safety control really does, follow the runbook for start, stop, restart and incidents |
| [Part IV: Extend](develop/README.md) | add a strategy or a provider, and read the ranked list of known gaps before planning work |
| [Appendix](appendix/README.md) | look up a term or an XTB protocol detail |

## Conventions

- Commands are shown as run from the repository root with the virtual environment active (`source .venv/bin/activate`).
- Defaults are quoted as `NAME=default`. Code locations are given as `path:line` relative to the repository root and refer to the formatted source as of this writing; a line may drift by a few positions after edits, the function name will not.
- Statements about library or protocol behaviour carry a status: **verified in code**, **verified against library source** (alpaca-py, websockets, python-dotenv upstream), **from third-party copies of the xAPI spec** (the official XTB documentation site no longer resolves), or **unverified**.
- Callouts: `> **Warning.**` for things that can cost money or data, `> **Note.**` for everything else.

> **Looking for a gentler introduction or the test suite?** The [User guide](../user/) explains the program without code. The [Tester guide](../tester/) explains how changes are checked and what blocks a merge.
