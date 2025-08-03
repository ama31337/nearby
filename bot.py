
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

    line = "ℹ Pool Info: {0}".format(val["account_id"])
    stake_line = f"{'Stake:':15} {stake:.1f} Ⓝ"
    rank_line = f"{'Rank by stake:':15} {rank_index}/{total_validators}"
    blk_line = f"{'Blocks:':15} {blk_prod}/{blk_exp} - {blk_pct:.1f}%"
    chk_line = f"{'Chunks:':15} {chk_prod}/{chk_exp} - {chk_pct:.1f}%"
    end_line = f"{'Endorsements:':15} {end_prod}/{end_exp} - {end_pct:.1f}%"

    return "\n".join([line, stake_line, rank_line, blk_line, chk_line, end_line])
async def with_retries(func, *args, **kwargs):
    for attempt in range(10):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            if "RPC returned empty response" in str(e) and attempt < 9:
                await asyncio.sleep(1)
                continue
            raise

#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import subprocess
import platform
import psutil
import datetime
import asyncio
import logging
import env
import telebot
from telebot import types
from py_near.providers import JsonProvider

# Setup
BOT_API_KEY = env.BotAPIKey
NEAR_NETWORK = getattr(env, "NEAR_NETWORK", "mainnet")
VALIDATOR_ACCOUNT = env.POOL_NAME
provider = JsonProvider(f"https://rpc.{NEAR_NETWORK}.near.org")
bot = telebot.TeleBot(BOT_API_KEY)

# Keyboards
markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
markup.row("ℹ My pool info", "⏩ Proposals", "⏩ Next")

# NEAR functions
async def get_pool_info():
    try:
        data = await with_retries(provider.get_validators) if provider else {}
        all_sets = data["current_validators"] + data["next_validators"] + data["current_proposals"]
        val = next((v for v in all_sets if v["account_id"] == VALIDATOR_ACCOUNT), None)
        if not val:
            return f"❌ {VALIDATOR_ACCOUNT} not found in any validator set."
        stake = int(val["stake"]) / 1e24
        all_validators = sorted(data['current_validators'], key=lambda x: int(x['stake']), reverse=True)
        rank = next((i + 1 for i, v in enumerate(all_validators) if v['account_id'] == VALIDATOR_ACCOUNT), None)

        blk_prod = int(val.get('num_produced_blocks', 0))
        blk_exp = int(val.get('num_expected_blocks', 1))
        blk_pct = round(100 * blk_prod / blk_exp, 1) if blk_exp != 0 else 0
        blk_emoji = '🟡' if blk_pct < 90 else ('🔴' if blk_pct < 80 else '')

        chk_prod = int(val.get('num_produced_chunks', 0))
        chk_exp = int(val.get('num_expected_chunks', 1))
        chk_pct = round(100 * chk_prod / chk_exp, 1) if chk_exp != 0 else 0
        chk_emoji = '🟡' if chk_pct < 90 else ('🔴' if chk_pct < 80 else '')

        end_prod = int(val.get('num_produced_endorsements', 0))
        end_exp = int(val.get('num_expected_endorsements', 1))
        end_pct = round(100 * end_prod / end_exp, 1) if end_exp != 0 else 0
        end_emoji = '🟡' if end_pct < 90 else ('🔴' if end_pct < 80 else '')

        return (
            f"ℹ Pool Info: {VALIDATOR_ACCOUNT}\n"
            f"Stake: {stake:.1f} Ⓝ\n" + f"Rank by stake: {rank}/{len(all_validators)}\n"
            f"Blocks: {blk_prod}/{blk_exp} - {blk_pct}% {blk_emoji}\n"
            f"Chunks: {chk_prod}/{chk_exp} - {chk_pct}% {chk_emoji}\n"
            f"Endorsements: {end_prod}/{end_exp} - {end_pct}% {end_emoji}"
        )
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Error: {e}"

async def get_next_validators():
    try:
        data = await with_retries(provider.get_validators) if provider else {}
        next_val = data["next_validators"]
        lines = [f"⏩ Next Validators ({len(next_val)} total):"]
        for v in next_val:
            if v["account_id"] != VALIDATOR_ACCOUNT:
                continue
            stake = int(v["stake"]) / 1e24
            lines.append(f"✅ {v['account_id']} | {stake:.1f} Ⓝ")
        return "\n".join(lines) if len(lines) > 1 else "ℹ Your validator is not in this set."
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Error: {e}"

async def get_proposals():
    try:
        data = await with_retries(provider.get_validators) if provider else {}
        proposals = data.get("current_proposals", [])
        lines = [f"📬 Proposals ({len(proposals)} total):"]
        for v in proposals:
            if v["account_id"] != VALIDATOR_ACCOUNT:
                continue
            stake = int(v["stake"]) / 1e24
            lines.append(f"✅ {v['account_id']} | {stake:.1f} Ⓝ")
        return "\n".join(lines) if len(lines) > 1 else "ℹ Your validator is not in this set."
    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"Error: {e}"

# Handlers
@bot.message_handler(commands=['start'])
def start(msg):
    bot.send_message(msg.chat.id, "Select option:", reply_markup=markup)

@bot.message_handler(func=lambda m: True)
def handle(msg):
    text = msg.text.strip()
    cid = msg.chat.id

    if text == "🎛 CPU":
        bot.send_message(cid, get_cpu())
    elif text == "🎚 RAM":
        bot.send_message(cid, get_ram())
    elif text == "💾 Disk usage":
        bot.send_message(cid, get_disks())
    elif text == "💽 Current disk i/o":
        bot.send_message(cid, get_disk_io())
    elif text == "🔛 Current network load":
        bot.send_message(cid, get_net_load())
    elif text == "Ⓝ NEAR tools":
        bot.send_message(cid, "Choose NEAR tool:", reply_markup=markup_near)
    elif text in ["ℹ My pool info", "My pool info"]:
        result = asyncio.run(get_pool_info())
        bot.send_message(cid, result)
    elif text in ["⏩ Next", "Next"]:
        result = asyncio.run(get_next_validators())
        bot.send_message(cid, result)
    elif text in ["⏩ Proposals", "Proposals"]:
        result = asyncio.run(get_proposals())
        bot.send_message(cid, result)
    elif text == "📋 Near logs":
        logs = subprocess.getoutput("journalctl -u neard.service -n 10")
        bot.send_message(cid, f"<code>{logs}</code>", parse_mode='HTML')
    elif text == "🏡 Main menu":
        bot.send_message(cid, "Main menu:", reply_markup=markup)
    else:
        bot.send_message(cid, "Unknown command")

bot.polling(none_stop=True)

# --- ALERTING LOGIC ---
alert_state = {
    "blocks": False,
    "chunks": False,
    "endorsements": False
}

async def check_alerts():
    try:
        result = await provider.get_validators()
        current_validators = result["current_validators"]
        val = next((v for v in current_validators if v["account_id"] == VALIDATOR_ACCOUNT), None)
        if not val:
            return

        alerts = []

        blk_prod = int(val.get("num_produced_blocks", 0))
        blk_exp = int(val.get("num_expected_blocks", 1))
        blk_missed = blk_exp - blk_prod
        if blk_missed >= MAX_MISSED_BLOCKS and not alert_state["blocks"]:
            alerts.append(f"📛 Missed blocks: {blk_missed} of {blk_exp}")
            alert_state["blocks"] = True
        elif blk_missed < MAX_MISSED_BLOCKS:
            alert_state["blocks"] = False

        chk_prod = int(val.get("num_produced_chunks", 0))
        chk_exp = int(val.get("num_expected_chunks", 1))
        chk_missed = chk_exp - chk_prod
        if chk_missed >= MAX_MISSED_CHUNKS and not alert_state["chunks"]:
            alerts.append(f"📛 Missed chunks: {chk_missed} of {chk_exp}")
            alert_state["chunks"] = True
        elif chk_missed < MAX_MISSED_CHUNKS:
            alert_state["chunks"] = False

        end_prod = int(val.get("num_produced_endorsements", 0))
        end_exp = int(val.get("num_expected_endorsements", 1))
        end_missed = end_exp - end_prod
        if end_missed >= MAX_MISSED_ENDORSEMENTS and not alert_state["endorsements"]:
            alerts.append(f"📛 Missed endorsements: {end_missed} of {end_exp}")
            alert_state["endorsements"] = True
        elif end_missed < MAX_MISSED_ENDORSEMENTS:
            alert_state["endorsements"] = False

        if alerts:
            try:
                msg = f"🚨 ALERT for {VALIDATOR_ACCOUNT} 🚨\n" + "\n".join(alerts)
                bot.send_message(AdminChatID, msg)
            except Exception:
                traceback.print_exc()

    except Exception:
        traceback.print_exc()

def start_monitor():
    while True:
        try:
            asyncio.run(check_alerts())
        except Exception:
            traceback.print_exc()
        time.sleep(30)


import threading

import asyncio
import threading
from concurrent.futures import Future

# Persistent event loop in a separate thread
loop = asyncio.new_event_loop()

def loop_runner():
    asyncio.set_event_loop(loop)
    loop.run_forever()

threading.Thread(target=loop_runner, daemon=True).start()

def run_async(coro):
    return asyncio.run_coroutine_threadsafe(coro, loop).result()


if __name__ == "__main__":
    alert_thread = threading.Thread(target=start_monitor, daemon=True)
    alert_thread.start()
    print("✅ Alert monitoring started in background")

    print("🤖 Starting Telegram bot polling...")
    bot.infinity_polling()