#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import subprocess
import threading
import time
import traceback

import env
import telebot
from telebot import types

from py_near.providers import JsonProvider

try:
    from py_near.exceptions.exceptions import RpcEmptyResponse
except Exception:
    class RpcEmptyResponse(Exception):
        pass


BOT_API_KEY = env.BotAPIKey
NEAR_NETWORK = getattr(env, "NEAR_NETWORK", "mainnet")
VALIDATOR_ACCOUNT = env.POOL_NAME

ADMIN_CHAT_ID = getattr(env, "AdminChatID", None)

MAX_MISSED_BLOCKS = int(getattr(env, "MAX_MISSED_BLOCKS", 999999))
MAX_MISSED_CHUNKS = int(getattr(env, "MAX_MISSED_CHUNKS", 999999))
MAX_MISSED_ENDORSEMENTS = int(getattr(env, "MAX_MISSED_ENDORSEMENTS", 999999))

RPC_URLS = list(getattr(env, "RPC_URLS", []))
if not RPC_URLS:
    RPC_URLS = [f"https://rpc.{NEAR_NETWORK}.near.org"]

CHECK_INTERVAL_SECONDS = int(getattr(env, "CHECK_INTERVAL_SECONDS", 30))


bot = telebot.TeleBot(BOT_API_KEY)

markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
markup.row("ℹ My pool info", "⏩ Proposals", "⏩ Next")
markup.row("📋 Near logs")


loop = asyncio.new_event_loop()


def loop_runner():
    asyncio.set_event_loop(loop)
    loop.run_forever()


threading.Thread(target=loop_runner, daemon=True).start()


def run_async(coro, timeout=45):
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    return fut.result(timeout=timeout)


_provider_lock = asyncio.Lock()
_provider = None
_rpc_index = 0


async def _get_provider():
    global _provider, _rpc_index
    async with _provider_lock:
        if _provider is None:
            _provider = JsonProvider(RPC_URLS[_rpc_index])
        return _provider


async def _rotate_provider():
    global _provider, _rpc_index
    async with _provider_lock:
        _rpc_index = (_rpc_index + 1) % len(RPC_URLS)
        _provider = JsonProvider(RPC_URLS[_rpc_index])


async def with_retries(coro_factory, attempts=6, base_sleep=1.0):
    last_exc = None
    for attempt in range(attempts):
        try:
            return await coro_factory()
        except RpcEmptyResponse as e:
            last_exc = e
        except Exception as e:
            last_exc = e

        await _rotate_provider()

        sleep_s = base_sleep * (2 ** attempt)
        if sleep_s > 20:
            sleep_s = 20
        await asyncio.sleep(sleep_s)

    if last_exc:
        raise last_exc
    raise RuntimeError("RPC error, unknown")


async def get_validators_data():
    async def do_call():
        provider = await _get_provider()
        return await provider.get_validators()

    return await with_retries(do_call)


def format_pool_info(val, stake, rank_index, total_validators):
    blk_prod = int(val.get("num_produced_blocks", 0))
    blk_exp = int(val.get("num_expected_blocks", 1))
    blk_pct = round((blk_prod / blk_exp) * 100, 1) if blk_exp else 0

    chk_prod = int(val.get("num_produced_chunks", 0))
    chk_exp = int(val.get("num_expected_chunks", 1))
    chk_pct = round((chk_prod / chk_exp) * 100, 1) if chk_exp else 0

    end_prod = int(val.get("num_produced_endorsements", 0))
    end_exp = int(val.get("num_expected_endorsements", 1))
    end_pct = round((end_prod / end_exp) * 100, 1) if end_exp else 0

    blk_emoji = "🔴" if blk_pct < 80 else ("🟡" if blk_pct < 90 else "")
    chk_emoji = "🔴" if chk_pct < 80 else ("🟡" if chk_pct < 90 else "")
    end_emoji = "🔴" if end_pct < 80 else ("🟡" if end_pct < 90 else "")

    line = f"ℹ Pool Info: {val.get('account_id', VALIDATOR_ACCOUNT)}"
    stake_line = f"{'Stake:':15} {stake:.1f} Ⓝ"
    rank_line = f"{'Rank by stake:':15} {rank_index}/{total_validators}"
    blk_line = f"{'Blocks:':15} {blk_prod}/{blk_exp} , {blk_pct:.1f}% {blk_emoji}"
    chk_line = f"{'Chunks:':15} {chk_prod}/{chk_exp} , {chk_pct:.1f}% {chk_emoji}"
    end_line = f"{'Endorsements:':15} {end_prod}/{end_exp} , {end_pct:.1f}% {end_emoji}"

    return "\n".join([line, stake_line, rank_line, blk_line, chk_line, end_line])


async def get_pool_info():
    data = await get_validators_data()

    all_sets = (
        data.get("current_validators", [])
        + data.get("next_validators", [])
        + data.get("current_proposals", [])
    )

    val = next((v for v in all_sets if v.get("account_id") == VALIDATOR_ACCOUNT), None)
    if not val:
        return f"❌ {VALIDATOR_ACCOUNT} not found in any validator set."

    stake = int(val.get("stake", 0)) / 1e24

    current_validators = data.get("current_validators", [])
    all_validators = sorted(current_validators, key=lambda x: int(x.get("stake", 0)), reverse=True)
    rank = next((i + 1 for i, v in enumerate(all_validators) if v.get("account_id") == VALIDATOR_ACCOUNT), None)
    if rank is None:
        rank = "?"

    return format_pool_info(val, stake, rank, len(all_validators))


async def get_next_validators():
    data = await get_validators_data()
    next_val = data.get("next_validators", [])
    match = [v for v in next_val if v.get("account_id") == VALIDATOR_ACCOUNT]
    if not match:
        return "ℹ Your validator is not in this set."

    v = match[0]
    stake = int(v.get("stake", 0)) / 1e24
    return f"⏩ Next Validators\n✅ {v.get('account_id')} , {stake:.1f} Ⓝ"


async def get_proposals():
    data = await get_validators_data()
    proposals = data.get("current_proposals", [])
    match = [v for v in proposals if v.get("account_id") == VALIDATOR_ACCOUNT]
    if not match:
        return "ℹ Your validator is not in proposals."

    v = match[0]
    stake = int(v.get("stake", 0)) / 1e24
    return f"📬 Proposals\n✅ {v.get('account_id')} , {stake:.1f} Ⓝ"


ALERT_BACKOFF = [0, 60, 300]
ALERT_STATE = {
    "blocks": {"last": 0, "stage": 0, "next_ts": 0.0},
    "chunks": {"last": 0, "stage": 0, "next_ts": 0.0},
    "endorsements": {"last": 0, "stage": 0, "next_ts": 0.0},
}


def reset_metric_state(metric):
    st = ALERT_STATE[metric]
    st["last"] = 0
    st["stage"] = 0
    st["next_ts"] = 0.0


def sync_state_for_value(metric, value, now_ts):
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


def mark_sent(metric, now_ts):
    st = ALERT_STATE[metric]
    st["stage"] += 1

    if st["stage"] >= len(ALERT_BACKOFF):
        st["next_ts"] = 0.0
        return

    st["next_ts"] = now_ts + float(ALERT_BACKOFF[st["stage"]])


async def check_alerts():
    if ADMIN_CHAT_ID is None:
        return

    now_ts = time.time()

    data = await get_validators_data()
    current_validators = data.get("current_validators", [])
    val = next((v for v in current_validators if v.get("account_id") == VALIDATOR_ACCOUNT), None)
    if not val:
        return

    blk_prod = int(val.get("num_produced_blocks", 0))
    blk_exp = int(val.get("num_expected_blocks", 0))
    blk_missed = max(0, blk_exp - blk_prod)

    chk_prod = int(val.get("num_produced_chunks", 0))
    chk_exp = int(val.get("num_expected_chunks", 0))
    chk_missed = max(0, chk_exp - chk_prod)

    end_prod = int(val.get("num_produced_endorsements", 0))
    end_exp = int(val.get("num_expected_endorsements", 0))
    end_missed = max(0, end_exp - end_prod)

    send_blocks = False
    send_chunks = False
    send_ends = False

    if blk_missed >= MAX_MISSED_BLOCKS:
        send_blocks = sync_state_for_value("blocks", blk_missed, now_ts)
    else:
        reset_metric_state("blocks")

    if chk_missed >= MAX_MISSED_CHUNKS:
        send_chunks = sync_state_for_value("chunks", chk_missed, now_ts)
    else:
        reset_metric_state("chunks")

    if end_missed >= MAX_MISSED_ENDORSEMENTS:
        send_ends = sync_state_for_value("endorsements", end_missed, now_ts)
    else:
        reset_metric_state("endorsements")

    if not (send_blocks or send_chunks or send_ends):
        return

    lines = [f"🚨 ALERT for {VALIDATOR_ACCOUNT}"]
    if send_blocks:
        lines.append(f"📛 Missed blocks: {blk_missed} of {blk_exp}")
    if send_chunks:
        lines.append(f"📛 Missed chunks: {chk_missed} of {chk_exp}")
    if send_ends:
        lines.append(f"📛 Missed endorsements: {end_missed} of {end_exp}")

    bot.send_message(ADMIN_CHAT_ID, "\n".join(lines))

    if send_blocks:
        mark_sent("blocks", now_ts)
    if send_chunks:
        mark_sent("chunks", now_ts)
    if send_ends:
        mark_sent("endorsements", now_ts)


def monitor_loop():
    while True:
        try:
            run_async(check_alerts(), timeout=60)
        except Exception:
            traceback.print_exc()
        time.sleep(CHECK_INTERVAL_SECONDS)


@bot.message_handler(commands=["start"])
def start(msg):
    bot.send_message(msg.chat.id, "Select option:", reply_markup=markup)


@bot.message_handler(func=lambda m: True)
def handle(msg):
    text = (msg.text or "").strip()
    cid = msg.chat.id

    try:
        if text in ["ℹ My pool info", "My pool info"]:
            bot.send_message(cid, run_async(get_pool_info()))
            return

        if text in ["⏩
