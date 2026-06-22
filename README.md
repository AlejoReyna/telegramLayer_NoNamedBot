# ANoNamed_bot

A Telegram notification bot for the **No Named Bot** crypto trading agent — part of the BNB Hackathon stack.

ANoNamed_bot watches the trading agent in real time and sends alerts on Telegram when anything important happens: a buy is executed, the market regime shifts, the data wallet runs low, or a risk event fires.

It is a pure sidecar — it never touches your wallet, keys, or trading logic. It only reads after-the-fact results and sends messages.

---

## What it notifies you about

| Event | Message | Rate limit |
|-------|---------|-----------|
| Buy executed | BUY EXECUTED with price, score, slippage, tx hash | None — every buy |
| BNB momentum detected | BNB MOMENTUM DETECTED with 1h/6h/24h change | Once per 4 hours |
| x402 data wallet low (<$1) | x402 DATA WALLET LOW | Always fires |
| x402 spend update (>$0.50 delta) | x402 Data Wallet Update | Once per hour |
| Kill switch triggered | RISK EVENT — KILL_SWITCH | None — always fires |
| Daily trade limit reached | DAILY TRADE LIMIT REACHED | Once per 24 hours |

---

## Setup

### 1. Create your Telegram bot

1. Open Telegram and message @BotFather
2. Send `/newbot` and follow the prompts
3. Copy the token it gives you — looks like `123456789:ABCdef...`

### 2. Get your chat ID

1. Message your new bot once (any text)
2. Open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser
3. Find `"chat":{"id":...}` — that number is your chat ID

### 3. Add to your `.env`

```env
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
TELEGRAM_CHAT_ID=123456789
```

---

## Usage

```python
from telegram_notifier import TelegramNotifier

notifier = TelegramNotifier(
    bot_token="123456789:ABCdef...",
    chat_id="123456789",
)

# Notify a buy
notifier.notify_buy(
    symbol="CAKE",
    amount_usdc=1.84,
    price=0.786,
    tx_hash="0xabc...",
    regime="trending_up",
    entry_score=68.5,
    daily_trade_count=2,
    max_daily=3,
    slippage_pct=0.0045,
)

# Notify a regime shift
notifier.notify_bnb_momentum(
    bnb_1h=0.012,
    bnb_6h=0.034,
    bnb_24h=0.051,
    regime="trending_up",
    score=4.2,
)

# Notify a risk event
notifier.notify_risk_event(
    event_type="KILL_SWITCH",
    portfolio_value=18.40,
    drawdown_pct=18.5,
    details="liquidating all positions",
)
```

---

## Design principles

- **Fail-safe** — every method swallows all exceptions; a Telegram outage never crashes the agent
- **Rate-limited** — momentum and balance alerts are throttled so you don't get spammed during volatile periods
- **Zero dependencies** — stdlib only (`urllib`, `json`, `time`); no `requests`, no `httpx`, nothing to install
- **Pure observer** — reads results after they happen, never before; no influence on any decision

---

## Used as a submodule

This repo is included in [BNBHacks-NoNamedBot](https://github.com/AlejoReyna/BNBHacks-NoNamedBot) as a git submodule at `src/common/telegram_notifier/`.

To pull it when cloning the main project:

```bash
git clone --recurse-submodules https://github.com/AlejoReyna/BNBHacks-NoNamedBot.git
# or, if already cloned:
git submodule update --init --recursive
```

---

## Tests

```bash
python -m pytest tests/ -v
```

16 tests, no external dependencies, no real API calls (all mocked).
