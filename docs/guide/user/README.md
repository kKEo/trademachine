# Welcome to tradesignals

This guide is for anyone who wants to watch a few US stocks and see when a simple trading rule says "buy" or "get out", without reading any code. It assumes you can open a terminal, copy a command, and create a free paper-trading account at a broker. Nothing here needs programming knowledge.

tradesignals is a small command-line program. It listens to live prices, builds price bars, asks a strategy whether to be in or out of a stock, and prints that decision. By default it only prints. Only when you ask for it does it send orders, and even then it refuses to touch a real-money account unless you insist with an extra flag.

| If you want to... | Read |
|---|---|
| understand what the program does in one screen | [What tradesignals does](what-it-does.md) |
| get a first run going (about 15 minutes) | [Quick start](quick-start.md) |
| start, stop, restart, and choose stocks and strategies | [Everyday use](everyday-use.md) |
| understand a line the program printed | [Reading the output](reading-the-output.md) |
| know what the two built-in strategies actually do | [Strategies explained](strategies.md) |
| know what keeps your money safe, and what does not | [Trust and safety](trust-and-safety.md) |
| decide whether this tool fits your situation | [Use cases and limits](use-cases.md) |
| fix something that went wrong | [FAQ and troubleshooting](faq.md) |
| look up a word | [Glossary](glossary.md) |

This is a prototype. It works, it has tests, and it has an honest list of things it does not do yet. Read [Trust and safety](trust-and-safety.md) before you connect anything other than a paper or demo account.

> **Looking for the internals, the settings reference or the tests?** The [Operator guide](../operator/) explains how the engine works and lists every setting with its default. The [Tester guide](../tester/) explains how changes are checked.
