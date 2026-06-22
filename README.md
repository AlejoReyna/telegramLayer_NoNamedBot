# ANoNamed_bot

ANoNamed_bot is the Telegram notification layer for the No Named Bot crypto trading agent, built for the BNB Hackathon.

It watches the trading agent in real time and sends alerts directly to Telegram when something worth knowing happens: a position is entered, the market regime shifts to a breakout, the x402 data wallet balance runs low, or a risk guardrail fires.

The bot is a pure sidecar. It has no influence over any trading decision — it only reads results after they happen and forwards them as messages. A notification failure never affects the agent; every call is fire-and-forget with full error swallowing.

## What it covers

- **Buy execution** — symbol, size, entry price, slippage, entry score, and on-chain tx hash
- **BNB momentum** — alerts when the market regime shifts to trending up, with BNB 1h/6h/24h change
- **x402 data wallet** — spend updates and a priority alert when the balance drops below $1
- **Risk events** — kill switch and drawdown guardrail triggers
- **Daily trade limit** — one alert per UTC day when the trade cap is reached

Rate limiting is built in so momentum and spend alerts do not spam during volatile periods. Buy and risk alerts always go through regardless of timing.

## Architecture

ANoNamed_bot is maintained as a standalone repository and included in the main [BNBHacks-NoNamedBot](https://github.com/AlejoReyna/BNBHacks-NoNamedBot) project as a git submodule at `src/common/telegram_notifier/`. It has no external dependencies beyond the Python standard library.
