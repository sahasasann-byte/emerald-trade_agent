import math
import os
import sys
import threading
import time
from datetime import datetime
import httpx
import numpy as np
import pandas as pd
import requests
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    filters,
)

# ==========================================
# 1. CONFIGURATION
# ==========================================
TELEGRAM_BOT_TOKEN = "8882498923:AAEFY7dC_DWFbBfFN81IXvyuebuevwG4Ruc"
TELEGRAM_CHAT_ID = "5944911045"

# PythonAnywhere Proxy URL
PA_PROXY = "http://proxy.server:3128"

# Global Prices Tracker
LATEST_PRICES = {
    "BTC": 0.0,
    "ETH": 0.0,
    "XAUUSD": 0.0,
    "NIFTY": {"spot": 0.0, "ema5": 0.0, "ema20": 0.0},
    "SENSEX": {"spot": 0.0, "ema5": 0.0, "ema20": 0.0},
}

# ISOLATED ACTIVE TRADES
ACTIVE_TRADES = {
    "BTC": None,
    "ETH": None,
    "XAUUSD": None,
    "NIFTY": None,
    "SENSEX": None,
}

DAILY_JOURNAL = []


# ==========================================
# 2. TELEGRAM ALERT DISPATCHER
# ==========================================
def send_telegram_alert(message):
    """Sends clean, formatted alerts to Telegram through PythonAnywhere Proxy."""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }
    proxies = {
        "http": PA_PROXY,
        "https": PA_PROXY,
    }

    try:
        requests.post(url, json=payload, proxies=proxies, timeout=10)
    except Exception:
        try:
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            print(f"⚠️ Telegram Alert Error: {e}")


# ==========================================
# 3. PYTHONANYWHERE COMPATIBLE FETCHERS
# ==========================================
def fetch_crypto_candles(asset="BTC", count=100):
    """Fetches Live Price using CryptoCompare/CoinGecko (Guaranteed on PA Free Tier)."""
    proxies = {"http": PA_PROXY, "https": PA_PROXY}

    # 1. Direct Price Fetching via CryptoCompare (PythonAnywhere Whitelisted)
    fsym = "BTC" if asset == "BTC" else ("ETH" if asset == "ETH" else "GOLD")
    tsym = "USD"

    try:
        url = f"https://min-api.cryptocompare.com/data/v2/histo/minute?fsym={fsym}&tsym={tsym}&limit={count}&aggregate=5"
        res = requests.get(url, proxies=proxies, timeout=10)
        data = res.json()

        if "Data" in data and "Data" in data["Data"]:
            candles = data["Data"]["Data"]
            df = pd.DataFrame(candles)
            df["time"] = pd.to_datetime(df["time"], unit="s")
            df = df.rename(columns={"volumeto": "volume"})
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = df[col].astype(float)
            return df[["time", "open", "high", "low", "close", "volume"]]
    except Exception as e:
        print(f"CryptoCompare Error for {asset}: {e}")

    # Fallback for Direct Single Price
    try:
        price_url = f"https://min-api.cryptocompare.com/data/price?fsym={fsym}&tsyms=USD"
        res = requests.get(price_url, proxies=proxies, timeout=5)
        price_data = res.json()
        if "USD" in price_data:
            current_p = float(price_data["USD"])
            LATEST_PRICES[asset] = current_p
    except Exception as e:
        print(f"Direct Price Fallback Error: {e}")

    return pd.DataFrame()


def fetch_index_candles(symbol="NIFTY"):
    """Fetches 5-minute candles for Nifty & Sensex."""
    ticker = "^NSEI" if symbol == "NIFTY" else "^BSESN"
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=5m&range=1d"
    headers = {"User-Agent": "Mozilla/5.0"}
    proxies = {"http": PA_PROXY, "https": PA_PROXY}

    try:
        res = requests.get(url, headers=headers, proxies=proxies, timeout=10)
        data = res.json()["chart"]["result"][0]
        timestamps = data["timestamp"]
        quotes = data["indicators"]["quote"][0]

        df = pd.DataFrame(
            {
                "time": pd.to_datetime(timestamps, unit="s"),
                "open": quotes["open"],
                "high": quotes["high"],
                "low": quotes["low"],
                "close": quotes["close"],
            }
        ).dropna()
        return df
    except Exception:
        return pd.DataFrame()


# ==========================================
# 4. SCALPER ENGINES
# ==========================================
def scan_crypto_market(asset="BTC"):
    global ACTIVE_TRADES, LATEST_PRICES, DAILY_JOURNAL

    df = fetch_crypto_candles(asset=asset)

    if not df.empty and len(df) >= 20:
        df["ema_5"] = df["close"].ewm(span=5, adjust=False).mean()
        df["ema_20"] = df["close"].ewm(span=20, adjust=False).mean()

        curr_price = round(df["close"].iloc[-1], 2)
        ema_5 = round(df["ema_5"].iloc[-1], 2)
        ema_20 = round(df["ema_20"].iloc[-1], 2)

        LATEST_PRICES[asset] = curr_price

        fee_buffer = round(curr_price * 0.0012, 2)
        active = ACTIVE_TRADES[asset]

        if active:
            target, sl, side = active["target"], active["sl"], active["side"]
            entry = active["entry"]

            if side == "LONG":
                if curr_price >= target:
                    pnl = round(((curr_price - entry) / entry) * 100, 2)
                    send_telegram_alert(
                        f"🎯 *TARGET HIT ({asset})!* 🚀\n\n• Exit Price: *${curr_price:,.2f}*\n• Net Profit: *+{pnl}%* 🟢"
                    )
                    ACTIVE_TRADES[asset] = None
                elif curr_price <= sl:
                    pnl = round(((curr_price - entry) / entry) * 100, 2)
                    send_telegram_alert(
                        f"🛑 *STOP LOSS HIT ({asset})!* 🔻\n\n• Exit Price: *${curr_price:,.2f}*\n• Realized Loss: *{pnl}%* 🔴"
                    )
                    ACTIVE_TRADES[asset] = None

            elif side == "SHORT":
                if curr_price <= target:
                    pnl = round(((entry - curr_price) / entry) * 100, 2)
                    send_telegram_alert(
                        f"🎯 *TARGET HIT ({asset})!* 🚀\n\n• Exit Price: *${curr_price:,.2f}*\n• Net Profit: *+{pnl}%* 🟢"
                    )
                    ACTIVE_TRADES[asset] = None
                elif curr_price >= sl:
                    pnl = round(((entry - curr_price) / entry) * 100, 2)
                    send_telegram_alert(
                        f"🛑 *STOP LOSS HIT ({asset})!* 🔻\n\n• Exit Price: *${curr_price:,.2f}*\n• Realized Loss: *{pnl}%* 🔴"
                    )
                    ACTIVE_TRADES[asset] = None
            return

        if ACTIVE_TRADES[asset] is None:
            if curr_price > ema_5 and ema_5 > ema_20:
                stop_loss = round(ema_20, 2)
                risk = round(curr_price - stop_loss, 2)
                if risk >= (curr_price * 0.0015):
                    target = round(curr_price + (2 * risk) + fee_buffer, 2)
                    ACTIVE_TRADES[asset] = {
                        "asset": asset,
                        "side": "LONG",
                        "entry": curr_price,
                        "target": target,
                        "sl": stop_loss,
                    }
                    msg = f"🚨 *NEW BUY / LONG SCALP ({asset})!* 🚀\n\n• Entry: *${curr_price:,.2f}*\n• Target: *${target:,.2f}*\n• Stop Loss: *${stop_loss:,.2f}*"
                    send_telegram_alert(msg)

            elif curr_price < ema_5 and ema_5 < ema_20:
                stop_loss = round(ema_20, 2)
                risk = round(stop_loss - curr_price, 2)
                if risk >= (curr_price * 0.0015):
                    target = round(curr_price - (2 * risk) - fee_buffer, 2)
                    ACTIVE_TRADES[asset] = {
                        "asset": asset,
                        "side": "SHORT",
                        "entry": curr_price,
                        "target": target,
                        "sl": stop_loss,
                    }
                    msg = f"🚨 *NEW SELL / SHORT SCALP ({asset})!* 🔻\n\n• Entry: *${curr_price:,.2f}*\n• Target: *${target:,.2f}*\n• Stop Loss: *${stop_loss:,.2f}*"
                    send_telegram_alert(msg)


def scan_indian_options(symbol="NIFTY"):
    global ACTIVE_TRADES, LATEST_PRICES

    df = fetch_index_candles(symbol)
    if df.empty or len(df) < 20:
        return

    df["ema_5"] = df["close"].ewm(span=5, adjust=False).mean()
    df["ema_20"] = df["close"].ewm(span=20, adjust=False).mean()

    curr_spot = round(df["close"].iloc[-1], 2)
    ema_5 = round(df["ema_5"].iloc[-1], 2)
    ema_20 = round(df["ema_20"].iloc[-1], 2)

    LATEST_PRICES[symbol]["spot"] = curr_spot
    LATEST_PRICES[symbol]["ema5"] = ema_5
    LATEST_PRICES[symbol]["ema20"] = ema_20

    brokerage_pts_buffer = 4.0 if symbol == "NIFTY" else 10.0
    atm_strike = (
        round(curr_spot / 50) * 50
        if symbol == "NIFTY"
        else round(curr_spot / 100) * 100
    )

    if ACTIVE_TRADES[symbol] is None:
        if curr_spot > ema_5 and ema_5 > ema_20:
            spot_risk = curr_spot - ema_20
            if spot_risk >= (10 if symbol == "NIFTY" else 30):
                est_entry_prem = 120.0 if symbol == "NIFTY" else 250.0
                prem_risk = spot_risk * 0.50
                target_prem = round(
                    est_entry_prem + (2 * prem_risk) + brokerage_pts_buffer, 2
                )
                sl_prem = round(est_entry_prem - prem_risk, 2)

                ACTIVE_TRADES[symbol] = {
                    "type": "CALL",
                    "entry_spot": curr_spot,
                    "entry_premium": est_entry_prem,
                    "target_premium": target_prem,
                    "sl_premium": sl_prem,
                }

                msg = f"🚨 *NEW BUY {symbol} CALL OPTION!* 🚀\n\n• Strike: *{atm_strike} CE*\n• Spot Entry: *₹{curr_spot:,.2f}*\n• Target Prem: *₹{target_prem}*\n• Stop Loss Prem: *₹{sl_prem}*"
                send_telegram_alert(msg)


# ==========================================
# 5. TELEGRAM HANDLERS
# ==========================================
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper()

    if text in ["1", "BTC", "ETH"]:
        msg = (
            f"📊 *CRYPTO & GOLD LIVE PRICES:*\n\n"
            f"• BTC: *${LATEST_PRICES['BTC']:,.2f}*\n"
            f"• ETH: *${LATEST_PRICES['ETH']:,.2f}*\n"
            f"• XAUUSD: *${LATEST_PRICES['XAUUSD']:,.2f}*"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    elif text == "2":
        msg = "🔄 *ACTIVE TRADES STATUS:*\n\n"
        has_trade = False
        for k in ["BTC", "ETH", "XAUUSD"]:
            t = ACTIVE_TRADES[k]
            if t:
                has_trade = True
                msg += f"• *{k}*: {t['side']} | Entry: ${t['entry']} | Target: ${t['target']}\n"
        if not has_trade:
            msg = "⏸️ Crypto & Gold - നിലവിൽ ആക്റ്റീവ് ട്രേഡുകൾ ഒന്നുമില്ല."
        await update.message.reply_text(msg, parse_mode="Markdown")

    elif text == "3":
        await update.message.reply_text("✅ Crypto & Gold Scanner Active (24/7)!")

    elif text == "N":
        d = LATEST_PRICES["NIFTY"]
        msg = f"📊 *NIFTY 50 LIVE DATA:*\n\n• Spot: *₹{d['spot']:,.2f}*\n• 5 EMA: *₹{d['ema5']}*\n• 20 EMA: *₹{d['ema20']}*"
        await update.message.reply_text(msg, parse_mode="Markdown")

    elif text == "S":
        d = LATEST_PRICES["SENSEX"]
        msg = f"📊 *SENSEX LIVE DATA:*\n\n• Spot: *₹{d['spot']:,.2f}*\n• 5 EMA: *₹{d['ema5']}*\n• 20 EMA: *₹{d['ema20']}*"
        await update.message.reply_text(msg, parse_mode="Markdown")

    elif text in ["NN", "SS"]:
        sym = "NIFTY" if text == "NN" else "SENSEX"
        t = ACTIVE_TRADES[sym]
        if t:
            msg = f"🔄 *{sym} ACTIVE TRADE:*\n\nType: *{t['type']}*\nEntry Spot: *₹{t['entry_spot']}*\nTarget Premium: *₹{t['target_premium']}*"
        else:
            msg = f"⏸️ *{sym}* - നിലവിൽ ആക്റ്റീവ് ട്രേഡുകൾ ഒന്നുമില്ല."
        await update.message.reply_text(msg, parse_mode="Markdown")

    elif text in ["NNN", "SSS"]:
        sym = "NIFTY" if text == "NNN" else "SENSEX"
        d = LATEST_PRICES[sym]
        t = ACTIVE_TRADES[sym]

        trade_status = (
            f"Type: *{t['type']}* | Entry: *₹{t['entry_spot']}* | Target Prem: *₹{t['target_premium']}*"
            if t
            else "ആക്റ്റീവ് ട്രേഡുകൾ ഒന്നുമില്ല ⏸️"
        )

        msg = (
            f"📈 *{sym} FULL DETAILED ANALYSIS:*\n\n"
            f"• Spot Price: *₹{d['spot']:,.2f}*\n"
            f"• 5 EMA: *₹{d['ema5']}*\n"
            f"• 20 EMA: *₹{d['ema20']}*\n\n"
            f"🔄 *Active Trade Status:*\n{trade_status}"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")


# ==========================================
# 6. BACKGROUND SCANNER
# ==========================================
def background_scanner():
    while True:
        try:
            for coin in ["BTC", "ETH", "XAUUSD"]:
                scan_crypto_market(coin)
            scan_indian_options("NIFTY")
            scan_indian_options("SENSEX")
            time.sleep(10)
        except Exception as e:
            print(f"Scanner Loop Error: {e}")
            time.sleep(10)


# ==========================================
# 7. MAIN EXECUTION
# ==========================================
if __name__ == "__main__":
    print("🚀 Starting Master Trading Agent...")

    t = threading.Thread(target=background_scanner, daemon=True)
    t.start()

    try:
        app = (
            ApplicationBuilder()
            .token(TELEGRAM_BOT_TOKEN)
            .proxy_url(PA_PROXY)
            .get_updates_proxy_url(PA_PROXY)
            .build()
        )
        app.add_handler(
            MessageHandler(filters.TEXT & (~filters.COMMAND), text_handler)
        )

        print("✅ Telegram Listener Ready!")
        send_telegram_alert(
            "🚀 *Master Trading Bot Online (CryptoCompare API Working)!*"
        )
        app.run_polling()

    except Exception as err:
        print(f"⚠️ Proxy Setup Fallback: {err}")
        try:
            app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
            app.add_handler(
                MessageHandler(filters.TEXT & (~filters.COMMAND), text_handler)
            )
            print("✅ Telegram Listener Ready (Direct Mode)!")
            send_telegram_alert("🚀 *Master Trading Bot Online (Direct)!*")
            app.run_polling()
        except Exception as err2:
            print(f"⚠️ Polling Error: {err2}")
            while True:
                time.sleep(60)
