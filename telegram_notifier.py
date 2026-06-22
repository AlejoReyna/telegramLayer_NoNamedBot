"""Telegram notification sidecar for Cascade AI trading agent.

This module is a pure observer: it reads after-the-fact trading results
and sends Telegram notifications. It never influences strategy decisions.

Interface contract:
    Imports: standard library only (no project dependencies).
    Exports: TelegramNotifier.
    Does not touch wallets, keys, strategy logic, or execution paths.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

# Rate-limit windows per notification type (seconds)
_DEFAULT_RATE_LIMITS: dict[str, float] = {
    "bnb_momentum": 14_400,   # 4 hours — TRENDING_UP is rare, avoid spam
    "x402_balance": 3_600,    # 1 hour — data spend is interesting but not urgent
    "buy": 0,                 # never rate-limit buys — they are rare by design
    "risk_event": 0,          # never rate-limit risk events — they are critical
    "daily_limit": 86_400,    # 24 hours — only once per day
}

# Minimum balance threshold for urgent x402 alert
_X402_LOW_BALANCE_USD = Decimal("1.0")
# Minimum spend delta to trigger a non-urgent x402 notification
_X402_MIN_SPEND_DELTA = Decimal("0.50")


@dataclass
class _RateLimitEntry:
    """Per-type rate limit state."""

    last_sent: float = 0.0
    count: int = 0


class TelegramNotifier:
    """Send formatted Telegram notifications with rate limiting and fail-safety.

    Usage:
        notifier = TelegramNotifier(bot_token="...", chat_id="...")
        notifier.notify_buy(...)
        notifier.notify_bnb_momentum(...)
        notifier.notify_x402_balance_if_changed(...)
        notifier.notify_risk_event(...)

    All methods are no-ops when ``bot_token`` is missing.
    All methods swallow exceptions so a notification failure never crashes the agent.
    """

    def __init__(
        self,
        bot_token: str | None,
        chat_id: str | None,
        *,
        base_rpc_url: str | None = None,
        rate_limits: dict[str, float] | None = None,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.base_rpc_url = base_rpc_url
        self._rate_limits = rate_limits or dict(_DEFAULT_RATE_LIMITS)
        self._rate_limit_state: dict[str, _RateLimitEntry] = {}
        # Cache previous x402 balance for delta detection
        self._prev_x402_balance: Decimal | None = None
        self._enabled = bool(self.bot_token and self.chat_id)
        if not self._enabled:
            LOGGER.debug("TelegramNotifier disabled: no bot_token or chat_id")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def notify_buy(
        self,
        *,
        symbol: str,
        amount_usdc: float,
        price: float,
        tx_hash: str | None,
        regime: str,
        entry_score: float | None,
        daily_trade_count: int,
        max_daily: int,
        slippage_pct: float | None = None,
    ) -> None:
        """Notify that a buy entry was successfully executed."""

        if not self._enabled:
            return
        if not self._rate_limit_ok("buy"):
            return

        tx_str = f"`{tx_hash[:10]}...{tx_hash[-4:]}`" if tx_hash else "`paper-trade`"
        slippage_str = f"{slippage_pct * 100:.2f}%" if slippage_pct is not None else "N/A"
        score_str = f"{entry_score:.1f}" if entry_score is not None else "N/A"

        msg = (
            f"🛒 *BUY EXECUTED — {symbol}*\n"
            f"Size: `${amount_usdc:.2f}` USDC → {symbol}\n"
            f"Price: `${price:.4f}`\n"
            f"Slippage: {slippage_str}\n"
            f"Entry score: {score_str}\n"
            f"Regime: `{regime}`\n"
            f"Daily trades: {daily_trade_count}/{max_daily}\n"
            f"Tx: {tx_str}"
        )
        self._send(msg)

    def notify_bnb_momentum(
        self,
        *,
        bnb_1h: float | None,
        bnb_6h: float | None,
        bnb_24h: float | None,
        regime: str,
        score: float,
        breadth: float | None = None,
    ) -> None:
        """Notify when the market regime shifts to TRENDING_UP."""

        if not self._enabled:
            return
        if not self._rate_limit_ok("bnb_momentum"):
            return

        b1 = f"{bnb_1h * 100:+.2f}%" if bnb_1h is not None else "N/A"
        b6 = f"{bnb_6h * 100:+.2f}%" if bnb_6h is not None else "N/A"
        b24 = f"{bnb_24h * 100:+.2f}%" if bnb_24h is not None else "N/A"
        br = f"{breadth * 100:.0f}%" if breadth is not None else "N/A"

        msg = (
            f"🚀 *BNB MOMENTUM DETECTED*\n"
            f"Regime: `{regime}` (score: `{score:.1f}`)\n"
            f"BNB 1h: {b1} | 6h: {b6} | 24h: {b24}\n"
            f"Universe breadth: {br} positive\n"
            f"The agent is now hunting for breakouts."
        )
        self._send(msg)

    def notify_x402_balance_if_changed(
        self,
        *,
        cycle_x402_cost: float,
        daily_spend_usdc: float,
        total_budget_usdc: float,
        daily_budget_usdc: float,
    ) -> None:
        """Notify x402 wallet changes when spend is significant or balance is low.

        This method attempts a live Base RPC read only when a notification
        would actually fire, so it does not add overhead to every cycle.
        """

        if not self._enabled:
            return

        # 1. Urgent: if the balance is critically low, always notify (ignoring rate limit)
        view = self._fetch_x402_view()
        current_balance = view.usdc_balance if view else None
        if current_balance is not None and current_balance < _X402_LOW_BALANCE_USD:
            remaining_total = float(current_balance)
            msg = (
                f"⚠️ *x402 DATA WALLET LOW*\n"
                f"Balance: `${remaining_total:.2f}` USDC\n"
                f"Total budget: `${total_budget_usdc:.2f}`\n"
                f"Daily spend: `${daily_spend_usdc:.2f}` / `${daily_budget_usdc:.2f}`\n"
                f"Agent will fall back to free keyless data."
            )
            self._send(msg, force=True)
            self._prev_x402_balance = current_balance
            return

        # 2. Normal: notify on meaningful spend delta (or first run)
        spend_delta = Decimal(str(cycle_x402_cost))
        if spend_delta < _X402_MIN_SPEND_DELTA and self._prev_x402_balance is not None:
            return
        if not self._rate_limit_ok("x402_balance"):
            return

        remaining_total = float(current_balance) if current_balance is not None else None
        remaining_str = f"`${remaining_total:.2f}`" if remaining_total is not None else "N/A"
        msg = (
            f"💰 *x402 Data Wallet Update*\n"
            f"Spend this cycle: `${cycle_x402_cost:.4f}`\n"
            f"Daily spend: `${daily_spend_usdc:.2f}` / `${daily_budget_usdc:.2f}`\n"
            f"Remaining total: {remaining_str} / `${total_budget_usdc:.2f}`\n"
            f"Status: {'✅ healthy' if (remaining_total is None or remaining_total > 1.0) else '⚠️ low'}"
        )
        self._send(msg)
        self._prev_x402_balance = current_balance

    def notify_risk_event(
        self,
        *,
        event_type: str,
        portfolio_value: float,
        drawdown_pct: float,
        details: str,
    ) -> None:
        """Notify critical risk events (kill switch, halt, daily limit reached)."""

        if not self._enabled:
            return
        if not self._rate_limit_ok("risk_event"):
            return

        emoji = "🛑" if "kill" in event_type.lower() else "⚠️"
        msg = (
            f"{emoji} *RISK EVENT — {event_type}*\n"
            f"Portfolio: `${portfolio_value:.2f}`\n"
            f"Drawdown: `{drawdown_pct:.2f}%`\n"
            f"Details: {details}"
        )
        self._send(msg)

    def notify_daily_limit(
        self,
        *,
        daily_trade_count: int,
        max_daily: int,
        portfolio_value: float,
    ) -> None:
        """Notify once per day when the daily trade limit is reached."""

        if not self._enabled:
            return
        if not self._rate_limit_ok("daily_limit"):
            return

        msg = (
            f"📛 *DAILY TRADE LIMIT REACHED*\n"
            f"Trades today: {daily_trade_count}/{max_daily}\n"
            f"Portfolio: `${portfolio_value:.2f}`\n"
            f"No new entries until UTC midnight."
        )
        self._send(msg)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_x402_view(self) -> Any | None:
        """Return x402 wallet view, or None on any failure."""

        try:
            from src.data.x402_wallet_view import fetch_x402_wallet_view

            return fetch_x402_wallet_view(base_rpc_url=self.base_rpc_url)
        except Exception as exc:
            LOGGER.debug("x402 wallet view fetch failed for notification: %s", exc)
            return None

    def _rate_limit_ok(self, kind: str) -> bool:
        """Return True if this notification kind is allowed to send now."""

        now = time.monotonic()
        limit = self._rate_limits.get(kind, 0)
        if limit <= 0:
            return True
        entry = self._rate_limit_state.get(kind)
        if entry is None:
            self._rate_limit_state[kind] = _RateLimitEntry(last_sent=now, count=1)
            return True
        if now - entry.last_sent < limit:
            return False
        entry.last_sent = now
        entry.count += 1
        return True

    def _send(self, text: str, *, force: bool = False) -> None:
        """Send a Telegram message. Swallows all errors."""

        if not self.bot_token or not self.chat_id:
            return
        if not force and not self._enabled:
            return

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = urllib.parse.urlencode(
            {
                "chat_id": self.chat_id,
                "text": text[:4000],
                "parse_mode": "Markdown",
                "disable_web_page_preview": "true",
            }
        ).encode("utf-8")

        try:
            urllib.request.urlopen(url, data=payload, timeout=5)
        except Exception as exc:
            LOGGER.warning("Telegram notification failed: %s", exc)
            self._log_error(text, exc)

    def _log_error(self, text: str, exc: Exception) -> None:
        """Persist failed notification for post-mortem debugging."""

        try:
            error_path = Path("logs/telegram_errors.jsonl")
            error_path.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "error": str(exc)[:200],
                "message_preview": text[:200],
            }
            with error_path.open("a", encoding="utf-8") as handle:
                json.dump(record, handle, sort_keys=True)
                handle.write("\n")
        except Exception:
            pass
