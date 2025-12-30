#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import signal
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Coroutine, TypeVar

from loguru import logger

import env
import telebot
from telebot import types

from py_near.providers import JsonProvider

# Type aliases
T = TypeVar("T")
ValidatorData = dict[str, Any]
ValidatorsResponse = dict[str, Any]

# Constants
YOCTO_NEAR = 10**24  # 1 NEAR = 10^24 yoctoNEAR
MAX_RETRY_SLEEP = 20
DEFAULT_ASYNC_TIMEOUT = 45

try:
    from py_near.exceptions.exceptions import RpcEmptyResponse
except Exception:
    class RpcEmptyResponse(Exception):
        pass


BOT_API_KEY = env.BotAPIKey
NEAR_NETWORK = getattr(env, "NEAR_NETWORK", "mainnet")
VALIDATOR_ACCOUNT = env.POOL_NAME

ADMIN_CHAT_ID = getattr(env, "AdminChatID", None)
ALLOWED_USERS = list(getattr(env, "ALLOWED_USERS", []))

# Alert thresholds: alert when performance drops below these percentages
MIN_BLOCK_PERCENT = float(getattr(env, "MIN_BLOCK_PERCENT", 90))
MIN_CHUNK_PERCENT = float(getattr(env, "MIN_CHUNK_PERCENT", 90))
MIN_ENDORSEMENT_PERCENT = float(getattr(env, "MIN_ENDORSEMENT_PERCENT", 90))

RPC_URLS = list(getattr(env, "RPC_URLS", []))
if not RPC_URLS:
    RPC_URLS = [f"https://rpc.{NEAR_NETWORK}.near.org"]

CHECK_INTERVAL_SECONDS = int(getattr(env, "CHECK_INTERVAL_SECONDS", 30))

# RPC failure tracking for alerts
RPC_CONSECUTIVE_FAILURES = 0
RPC_FAILURE_ALERT_THRESHOLD = 3
RPC_FAILURE_ALERTED = False

# Shutdown flag
_shutdown_event = threading.Event()

# Configure logging
LOG_FILE = getattr(env, "LOG_FILE", "nearby.log")
logger.remove()  # Remove default handler
logger.add(sys.stderr, level="INFO", format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>")
logger.add(LOG_FILE, rotation="10 MB", retention="7 days", level="DEBUG", format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}")

bot = telebot.TeleBot(BOT_API_KEY)


def is_authorized(user_id: int) -> bool:
    """Check if user is authorized to use the bot."""
    if not ALLOWED_USERS:
        return True  # If not configured, allow everyone
    return user_id in ALLOWED_USERS


markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
markup.row("ℹ My pool info", "⏩ Proposals", "⏩ Next")
markup.row("📋 Near logs")


loop = asyncio.new_event_loop()


def loop_runner() -> None:
    """Run the async event loop in a separate thread."""
    asyncio.set_event_loop(loop)
    loop.run_forever()


threading.Thread(target=loop_runner, daemon=True).start()


def run_async(coro: Coroutine[Any, Any, T], timeout: float = DEFAULT_ASYNC_TIMEOUT) -> T:
    """Run async coroutine from sync context."""
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut.result(timeout=timeout)


_provider_lock = asyncio.Lock()
_provider: JsonProvider | None = None
_rpc_index: int = 0


async def _get_provider() -> JsonProvider:
    """Get or create RPC provider instance."""
    global _provider, _rpc_index
    async with _provider_lock:
        if _provider is None:
            _provider = JsonProvider(RPC_URLS[_rpc_index])
        return _provider


async def _rotate_provider() -> None:
    """Rotate to next RPC provider in the list."""
    global _provider, _rpc_index
    async with _provider_lock:
        _rpc_index = (_rpc_index + 1) % len(RPC_URLS)
        _provider = JsonProvider(RPC_URLS[_rpc_index])


async def with_retries(
    coro_factory: Callable[[], Coroutine[Any, Any, T]],
    attempts: int = 6,
    base_sleep: float = 1.0
) -> T:
    """Execute async function with retries and exponential backoff."""
    global RPC_CONSECUTIVE_FAILURES, RPC_FAILURE_ALERTED

    last_exc: Exception | None = None
    for attempt in range(attempts):
        try:
            result = await coro_factory()
            # Success - reset failure counter
            if RPC_CONSECUTIVE_FAILURES > 0:
                logger.info(f"RPC connection restored after {RPC_CONSECUTIVE_FAILURES} failures")
                if RPC_FAILURE_ALERTED and ADMIN_CHAT_ID:
                    try:
                        bot.send_message(ADMIN_CHAT_ID, "✅ RPC connection restored")
                    except Exception:
                        pass
            RPC_CONSECUTIVE_FAILURES = 0
            RPC_FAILURE_ALERTED = False
            return result
        except RpcEmptyResponse as e:
            last_exc = e
            logger.warning(f"RPC empty response (attempt {attempt + 1}/{attempts}): {e}")
        except Exception as e:
            last_exc = e
            logger.warning(f"RPC error (attempt {attempt + 1}/{attempts}): {e}")

        await _rotate_provider()
        logger.debug(f"Rotated to RPC: {RPC_URLS[_rpc_index]}")

        sleep_s = min(base_sleep * (2 ** attempt), MAX_RETRY_SLEEP)
        await asyncio.sleep(sleep_s)

    # All attempts failed
    RPC_CONSECUTIVE_FAILURES += 1
    logger.error(f"RPC failed after {attempts} attempts. Consecutive failures: {RPC_CONSECUTIVE_FAILURES}")

    # Send alert if threshold reached and not yet alerted
    if RPC_CONSECUTIVE_FAILURES >= RPC_FAILURE_ALERT_THRESHOLD and not RPC_FAILURE_ALERTED:
        if ADMIN_CHAT_ID:
            try:
                bot.send_message(
                    ADMIN_CHAT_ID,
                    f"🔴 RPC CONNECTION FAILED\n"
                    f"All {len(RPC_URLS)} RPC endpoints unreachable\n"
                    f"Consecutive failures: {RPC_CONSECUTIVE_FAILURES}\n"
                    f"Last error: {last_exc}"
                )
                RPC_FAILURE_ALERTED = True
                logger.info("RPC failure alert sent to admin")
            except Exception as e:
                logger.error(f"Failed to send RPC alert: {e}")

    if last_exc:
        raise last_exc
    raise RuntimeError("RPC error, unknown")


async def get_validators_data() -> ValidatorsResponse:
    """Fetch validators data from NEAR RPC with retries."""
    async def do_call() -> ValidatorsResponse:
        provider = await _get_provider()
        return await provider.get_validators()

    return await with_retries(do_call)


def find_validator(validators: list[ValidatorData], account_id: str) -> ValidatorData | None:
    """Find validator by account_id in a list of validators."""
    return next((v for v in validators if v.get("account_id") == account_id), None)


def stake_to_near(stake: int | str) -> float:
    """Convert stake from yoctoNEAR to NEAR."""
    return int(stake) / YOCTO_NEAR


def calculate_percentage(produced: int, expected: int) -> float:
    """Calculate percentage of produced vs expected, returns 0 if expected is 0."""
    if expected == 0:
        return 0.0
    return round((produced / expected) * 100, 1)


def get_status_emoji(percentage: float, warning_threshold: int = 90, critical_threshold: int = 80) -> str:
    """Get emoji based on percentage thresholds."""
    if percentage < critical_threshold:
        return "🔴"
    if percentage < warning_threshold:
        return "🟡"
    return ""


def format_pool_info(val: ValidatorData, stake: float, rank_index: int | str, total_validators: int) -> str:
    """Format validator pool info for display."""
    blk_prod = int(val.get("num_produced_blocks", 0))
    blk_exp = int(val.get("num_expected_blocks", 0)) or 1
    blk_pct = calculate_percentage(blk_prod, blk_exp)

    chk_prod = int(val.get("num_produced_chunks", 0))
    chk_exp = int(val.get("num_expected_chunks", 0)) or 1
    chk_pct = calculate_percentage(chk_prod, chk_exp)

    end_prod = int(val.get("num_produced_endorsements", 0))
    end_exp = int(val.get("num_expected_endorsements", 0)) or 1
    end_pct = calculate_percentage(end_prod, end_exp)

    return "\n".join([
        f"ℹ Pool Info: {val.get('account_id', VALIDATOR_ACCOUNT)}",
        f"{'Stake:':15} {stake:.1f} Ⓝ",
        f"{'Rank by stake:':15} {rank_index}/{total_validators}",
        f"{'Blocks:':15} {blk_prod}/{blk_exp} , {blk_pct:.1f}% {get_status_emoji(blk_pct)}",
        f"{'Chunks:':15} {chk_prod}/{chk_exp} , {chk_pct:.1f}% {get_status_emoji(chk_pct)}",
        f"{'Endorsements:':15} {end_prod}/{end_exp} , {end_pct:.1f}% {get_status_emoji(end_pct)}",
    ])


async def get_pool_info() -> str:
    """Get formatted pool info for the configured validator."""
    data = await get_validators_data()

    all_sets = (
        data.get("current_validators", [])
        + data.get("next_validators", [])
        + data.get("current_proposals", [])
    )

    val = find_validator(all_sets, VALIDATOR_ACCOUNT)
    if not val:
        return f"❌ {VALIDATOR_ACCOUNT} not found in any validator set."

    stake = stake_to_near(val.get("stake", 0))

    current_validators = data.get("current_validators", [])
    sorted_validators = sorted(current_validators, key=lambda x: int(x.get("stake", 0)), reverse=True)

    rank: int | str = "?"
    for i, v in enumerate(sorted_validators):
        if v.get("account_id") == VALIDATOR_ACCOUNT:
            rank = i + 1
            break

    return format_pool_info(val, stake, rank, len(sorted_validators))


async def get_next_validators() -> str:
    """Check if validator is in next validators set."""
    data = await get_validators_data()
    val = find_validator(data.get("next_validators", []), VALIDATOR_ACCOUNT)

    if not val:
        return "ℹ Your validator is not in this set."

    stake = stake_to_near(val.get("stake", 0))
    return f"⏩ Next Validators\n✅ {val.get('account_id')} , {stake:.1f} Ⓝ"


async def get_proposals() -> str:
    """Check if validator is in current proposals."""
    data = await get_validators_data()
    val = find_validator(data.get("current_proposals", []), VALIDATOR_ACCOUNT)

    if not val:
        return "ℹ Your validator is not in proposals."

    stake = stake_to_near(val.get("stake", 0))
    return f"📬 Proposals\n✅ {val.get('account_id')} , {stake:.1f} Ⓝ"


ALERT_BACKOFF: list[int] = [0, 60, 300]  # Seconds between repeated alerts
ALERT_STATE: dict[str, dict[str, int | float]] = {
    "blocks": {"last": 0, "stage": 0, "next_ts": 0.0},
    "chunks": {"last": 0, "stage": 0, "next_ts": 0.0},
    "endorsements": {"last": 0, "stage": 0, "next_ts": 0.0},
}


def reset_metric_state(metric: str) -> None:
    """Reset alert state for a metric."""
    st = ALERT_STATE[metric]
    st["last"] = 0
    st["stage"] = 0
    st["next_ts"] = 0.0


def sync_state_for_value(metric: str, value: int, now_ts: float) -> bool:
    """Update state and determine if alert should be sent."""
    st = ALERT_STATE[metric]

    if value <= 0:
        reset_metric_state(metric)
        return False

    if value != st["last"]:
        st["last"] = value
        st["stage"] = 0
        st["next_ts"] = now_ts

    if st["stage"] >= len(ALERT_BACKOFF):
        return False

    return now_ts >= st["next_ts"]


def mark_sent(metric: str, now_ts: float) -> None:
    """Mark alert as sent and schedule next alert."""
    st = ALERT_STATE[metric]
    st["stage"] = int(st["stage"]) + 1

    if st["stage"] >= len(ALERT_BACKOFF):
        st["next_ts"] = 0.0
        return

    st["next_ts"] = now_ts + float(ALERT_BACKOFF[int(st["stage"])])


async def check_alerts() -> None:
    """Check validator metrics and send alerts if performance drops below thresholds."""
    if ADMIN_CHAT_ID is None:
        return

    now_ts = time.time()

    data = await get_validators_data()
    val = find_validator(data.get("current_validators", []), VALIDATOR_ACCOUNT)
    if not val:
        return

    # Calculate percentages
    blk_prod = int(val.get("num_produced_blocks", 0))
    blk_exp = int(val.get("num_expected_blocks", 0))
    blk_pct = calculate_percentage(blk_prod, blk_exp) if blk_exp > 0 else 100.0

    chk_prod = int(val.get("num_produced_chunks", 0))
    chk_exp = int(val.get("num_expected_chunks", 0))
    chk_pct = calculate_percentage(chk_prod, chk_exp) if chk_exp > 0 else 100.0

    end_prod = int(val.get("num_produced_endorsements", 0))
    end_exp = int(val.get("num_expected_endorsements", 0))
    end_pct = calculate_percentage(end_prod, end_exp) if end_exp > 0 else 100.0

    send_blocks = False
    send_chunks = False
    send_ends = False

    # Alert when percentage drops below threshold
    # Use inverted percentage (100 - pct) for state tracking so higher = worse
    if blk_pct < MIN_BLOCK_PERCENT:
        send_blocks = sync_state_for_value("blocks", int(100 - blk_pct), now_ts)
    else:
        reset_metric_state("blocks")

    if chk_pct < MIN_CHUNK_PERCENT:
        send_chunks = sync_state_for_value("chunks", int(100 - chk_pct), now_ts)
    else:
        reset_metric_state("chunks")

    if end_pct < MIN_ENDORSEMENT_PERCENT:
        send_ends = sync_state_for_value("endorsements", int(100 - end_pct), now_ts)
    else:
        reset_metric_state("endorsements")

    if not (send_blocks or send_chunks or send_ends):
        return

    lines = [f"🚨 ALERT for {VALIDATOR_ACCOUNT}"]
    if send_blocks:
        lines.append(f"📛 Blocks: {blk_pct:.1f}% ({blk_prod}/{blk_exp}) — below {MIN_BLOCK_PERCENT}%")
    if send_chunks:
        lines.append(f"📛 Chunks: {chk_pct:.1f}% ({chk_prod}/{chk_exp}) — below {MIN_CHUNK_PERCENT}%")
    if send_ends:
        lines.append(f"📛 Endorsements: {end_pct:.1f}% ({end_prod}/{end_exp}) — below {MIN_ENDORSEMENT_PERCENT}%")

    bot.send_message(ADMIN_CHAT_ID, "\n".join(lines))

    if send_blocks:
        mark_sent("blocks", now_ts)
    if send_chunks:
        mark_sent("chunks", now_ts)
    if send_ends:
        mark_sent("endorsements", now_ts)


def monitor_loop() -> None:
    """Main monitoring loop that checks alerts periodically."""
    logger.info("Monitor loop started")
    while not _shutdown_event.is_set():
        try:
            run_async(check_alerts(), timeout=60)
        except Exception as e:
            logger.exception(f"Monitor loop error: {e}")
        # Use event wait instead of sleep for faster shutdown
        _shutdown_event.wait(timeout=CHECK_INTERVAL_SECONDS)
    logger.info("Monitor loop stopped")


@bot.message_handler(commands=["start"])
def start(msg):
    if not is_authorized(msg.from_user.id):
        logger.warning(f"Unauthorized access attempt: user_id={msg.from_user.id}, username={msg.from_user.username}")
        bot.send_message(msg.chat.id, "⛔ Access denied. Contact administrator.")
        return
    logger.info(f"User {msg.from_user.id} started bot")
    bot.send_message(msg.chat.id, "Select option:", reply_markup=markup)


@bot.message_handler(func=lambda m: True)
def handle(msg):
    if not is_authorized(msg.from_user.id):
        logger.warning(f"Unauthorized access attempt: user_id={msg.from_user.id}, username={msg.from_user.username}")
        bot.send_message(msg.chat.id, "⛔ Access denied. Contact administrator.")
        return

    text = (msg.text or "").strip()
    cid = msg.chat.id
    logger.debug(f"Command from user {msg.from_user.id}: {text}")

    try:
        if text in ["ℹ My pool info", "My pool info"]:
            bot.send_message(cid, run_async(get_pool_info()))
            return

        if text in ["⏩ Next", "Next"]:
            bot.send_message(cid, run_async(get_next_validators()))
            return

        if text in ["⏩ Proposals", "Proposals"]:
            bot.send_message(cid, run_async(get_proposals()))
            return

        if text == "📋 Near logs":
            try:
                result = subprocess.run(
                    ["journalctl", "-u", "neard.service", "-n", "10", "--no-pager"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                logs = result.stdout or result.stderr or "No logs available"
            except subprocess.TimeoutExpired:
                logs = "Timeout while fetching logs"
            except FileNotFoundError:
                logs = "journalctl not found"
            bot.send_message(cid, f"<code>{logs}</code>", parse_mode="HTML")
            return

        bot.send_message(cid, "Unknown command", reply_markup=markup)
    except Exception as e:
        logger.exception(f"Error handling command '{text}' from user {msg.from_user.id}: {e}")
        bot.send_message(cid, f"Error: {e}")


def shutdown_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    sig_name = signal.Signals(signum).name
    logger.info(f"Received {sig_name}, shutting down...")

    # Signal monitor loop to stop
    _shutdown_event.set()

    # Stop the event loop
    loop.call_soon_threadsafe(loop.stop)

    # Stop bot polling
    bot.stop_polling()

    logger.info("Shutdown complete")
    sys.exit(0)


if __name__ == "__main__":
    # Register signal handlers
    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    logger.info(f"Starting NEAR validator monitor bot for {VALIDATOR_ACCOUNT}")
    logger.info(f"Network: {NEAR_NETWORK}, RPC endpoints: {len(RPC_URLS)}")
    logger.info(f"Check interval: {CHECK_INTERVAL_SECONDS}s, Admin chat: {ADMIN_CHAT_ID or 'not set'}")

    # Start monitor loop in background thread
    monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
    monitor_thread.start()

    # Start bot polling (blocks until stopped)
    try:
        bot.infinity_polling(timeout=30, long_polling_timeout=30)
    except Exception as e:
        logger.exception(f"Bot polling error: {e}")
    finally:
        _shutdown_event.set()
        logger.info("Bot stopped")
