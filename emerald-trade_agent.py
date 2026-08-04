import os
import sys
import time
import logging
import threading
from datetime import datetime, timedelta
import pytz
import numpy as np
import pandas as pd
import requests
from flask import Flask

# FYERS API v3 Imports
try:
    from fyers_apiv3 import fyersModel
    FYERS_AVAILABLE = True
except ImportError:
    FYERS_AVAILABLE = False

# Telegram Bot Imports
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# Setup Logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# ==========================================
# 1. CREDENTIALS & CONFIGURATION
# ==========================================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8866649004:AAHuRrhqCHqRq0Ucb1i_UyTCG2B5nKOCkps")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "5944911045")

FYERS_CLIENT_ID = os.environ.get("FYERS_CLIENT_ID", "KDE60BKD5D-100")
HARDCODED_ACCESS_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhdWQiOlsiZDoxIiwiZDoyIiwieDowIiwieDoxIl0sImF0X2hhc2giOiJnQUFBQUFCcWNoRG5hZ3B6blBPLWpmYkVLYzFtdFhZcmszWnFSYTVGYXZLS0xQY2xYUlYzTnpTc2JxTzR5WTNZR3E2cHduNm1rU0J4VEJDRDAyVHlUd1lkZU1uaDkwWEVBVDRuYlEzbWNXU2UzRzhCTGZLb3RuRT0iLCJkaXNwbGF5X25hbWUiOiIiLCJvbXMiOiJLMSIsImhzbV9rZXkiOiJhZGFkMzlhZDQwOWUxZTcwNjU5ZDdiNDI4N2ZiNGFiZjE5YzlmN2ZkOGYwMzhjMDIwYzdhYzNiNCIsImlzRGRwaUVuYWBsZWQiOiJOIiwiaXNNdGZFbmFibGVkIjoiTiIsImZ5X2lkIjoiRkFLMzc1MDIiLCJhcHBUeXBlIjoxMDAsImV4cCI6MTc4NTg4OTgwMCwiaWF0IjoxNzg1ODYwMzI3LCJpc3MiOiJhcGkuZnllcnMuaW4iLCJuYmYiOjE3ODU4NjAzMjcsInN1YiI6ImFjY2Vzc190b2tlbiJ9.aFqvqHBsMSNHdMK4xANDBx2I2lUbPPSqCWzkQyIkIdA"

ASSET_CONFIG = {
    "NIFTY": {"fyers": "NSE:NIFTY50-INDEX", "yahoo": "^NSEI", "step": 50},
    "BANK NIFTY": {"fyers": "NSE:NIFTYBANK-INDEX", "yahoo": "^NSEBANK", "step": 100},
    "SENSEX": {"fyers": "BSE:SENSEX-INDEX", "yahoo": "^BSESN", "step": 100},
    "CRUDE OIL": {"fyers": "MCX:CRUDEOIL26AUGFUT", "yahoo": "CL=F", "step": 10},
    "NATURAL GAS": {"fyers": "MCX:NATURALGAS26AUGFUT", "yahoo": "NG=F", "step": 1},
    "GOLD": {"fyers": "MCX:GOLD26OCTFUT", "yahoo": "GC=F", "step": 100},
    "SILVER": {"fyers": "MCX:SILVER26SEPFUT", "yahoo": "SI=F", "step": 100},
}

fyers = None

# Tracks the last sent signal state for each asset to prevent duplicate spam
LAST_SIGNAL_STATE = {asset: None for asset in ASSET_CONFIG}

def initialize_fyers():
    global fyers
    if not FYERS_AVAILABLE:
        logging.warning("⚠️ Fyers SDK not available. Using Yahoo Finance Data Engine.")
        return
    token = os.environ.get("FYERS_ACCESS_TOKEN", HARDCODED_ACCESS_TOKEN)
    if token:
        try:
            fyers = fyersModel.FyersModel(client_id=FYERS_CLIENT_ID, is_async=False, token=token, log_path="")
            logging.info("✅ Fyers API Initialized Successfully!")
        except Exception as e:
            logging.error(f"❌ Fyers Init Failed: {e}")

# ==========================================
# 2. TELEGRAM ALERT DISPATCHER
# ==========================================
def send_telegram_alert(message):
    """Direct HTTP dispatcher for auto-generated background alerts"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logging.error(f"⚠️ Telegram Dispatch Error: {e}")

# ==========================================
# 3. DATA ENGINE (FYERS + FALLBACK)
# ==========================================
def fetch_live_ohlc(asset_name):
    config = ASSET_CONFIG.get(asset_name)
    if not config:
        return pd.DataFrame()

    if fyers:
        try:
            tz = pytz.timezone("Asia/Kolkata")
            now = datetime.now(tz)
            data = {
                "symbol": config["fyers"],
                "resolution": "3",
                "date_format": "1",
                "range_from": (now - timedelta(days=3)).strftime("%Y-%m-%d"),
                "range_to": now.strftime("%Y-%m-%d"),
                "cont_flag": "1"
            }
            res = fyers.history(data=data)
            if res.get("s") == "ok" and res.get("candles"):
                df = pd.DataFrame(res["candles"], columns=["timestamp", "open", "high", "low", "close", "volume"])
                df["time"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
                return df
        except Exception as e:
            pass

    try:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{config['yahoo']}?range=2d&interval=2m"
        headers = {'User-Agent': 'Mozilla/5.0'}
        response = requests.get(url, headers=headers, timeout=5)
        if response.status_code == 200:
            result = response.json()["chart"]["result"][0]
            timestamps = result["timestamp"]
            quote = result["indicators"]["quote"][0]
            df = pd.DataFrame({
                "timestamp": timestamps,
                "open": quote["open"],
                "high": quote["high"],
                "low": quote["low"],
                "close": quote["close"],
                "volume": quote["volume"]
            }).dropna()
            df["time"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
            return df
    except Exception:
        pass

    return pd.DataFrame()

def calculate_technical_indicators(df):
    df["ema_5"] = df["close"].ewm(span=5, adjust=False).mean()
    df["ema_9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["tp"] = (df["high"] + df["low"] + df["close"]) / 3
    df["vwap"] = (df["tp"] * df["volume"]).cumsum() / df["volume"].cumsum().replace(0, 1)
    df["tr"] = np.maximum(df["high"] - df["low"], np.abs(df["high"] - df["close"].shift(1)))
    df["atr"] = df["tr"].rolling(14).mean()
    return df

# ==========================================
# 4. SIGNAL & ANALYSIS ENGINE
# ==========================================
def analyze_asset_scalp(asset_name, is_auto_scan=False):
    global LAST_SIGNAL_STATE

    df = fetch_live_ohlc(asset_name)
    if df.empty or len(df) < 15:
        if not is_auto_scan:
            return f"⚠️ **Data Fetch Error:** Unable to retrieve live price for `{asset_name}`."
        return None

    df = calculate_technical_indicators(df)
    latest = df.iloc[-1]
    
    curr_price = round(float(latest["close"]), 2)
    ema_5 = round(float(latest["ema_5"]), 2)
    ema_9 = round(float(latest["ema_9"]), 2)
    vwap = round(float(latest["vwap"]), 2)
    atr = round(float(latest["atr"]), 2) if not np.isnan(latest["atr"]) else 15.0

    step = ASSET_CONFIG[asset_name]["step"]
    atm_strike = round(curr_price / step) * step

    sl_dist = max(round(atr * 1.2, 2), 12.0)
    tp_dist = round(sl_dist * 1.9, 2)

    current_signal = "NEUTRAL"

    if curr_price > vwap and ema_5 > ema_9:
        current_signal = "BUY"
        ce_strike = int(atm_strike - step if asset_name in ["NIFTY", "BANK NIFTY", "SENSEX"] else atm_strike)
        option_pick = f"`{ce_strike} CALL (CE)`"
        entry_zone = f"₹{curr_price - 3:,.2f} - ₹{curr_price + 3:,.2f}"
        sl = round(curr_price - sl_dist, 2)
        tp = round(curr_price + tp_dist, 2)
        signal_header = "🚨 **NEW AUTOMATED SCALP ALERT: BUY** 🟢"
        bias_desc = "Spot crossed above VWAP with 5/9 EMA bullish expansion."
    elif curr_price < vwap and ema_5 < ema_9:
        current_signal = "SELL"
        pe_strike = int(atm_strike + step if asset_name in ["NIFTY", "BANK NIFTY", "SENSEX"] else atm_strike)
        option_pick = f"`{pe_strike} PUT (PE)`"
        entry_zone = f"₹{curr_price - 3:,.2f} - ₹{curr_price + 3:,.2f}"
        sl = round(curr_price + sl_dist, 2)
        tp = round(curr_price - tp_dist, 2)
        signal_header = "🚨 **NEW AUTOMATED SCALP ALERT: SELL** 🔴"
        bias_desc = "Spot breakdown below VWAP with 5/9 EMA bearish expansion."
    else:
        if is_auto_scan:
            LAST_SIGNAL_STATE[asset_name] = "NEUTRAL"
            return None
        return (
            f"⚡ **LIVE MARKET ANALYSIS: {asset_name}**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"• Current Spot Price: **₹{curr_price:,.2f}**\n"
            f"• Live VWAP: **₹{vwap:,.2f}** | 5 EMA: **₹{ema_5:,.2f}**\n"
            f"• Market Status: **NO TRADE ZONE (Consolidation)**\n\n"
            f"💡 *Price is trapped near VWAP. Awaiting breakout.*"
        )

    # In Auto Scan mode, only trigger alert if signal changed
    if is_auto_scan:
        if LAST_SIGNAL_STATE[asset_name] == current_signal:
            return None  # Already alerted for this signal move
        LAST_SIGNAL_STATE[asset_name] = current_signal

    report = (
        f"{signal_header}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"• **Asset:** `{asset_name}`\n"
        f"• **Recommended Option:** {option_pick}\n"
        f"• **Current Spot Price:** ₹{curr_price:,.2f}\n"
        f"• **Trigger Reason:** {bias_desc}\n\n"
        f"🎯 **EXECUTION & RISK LEVELS (1:1.9 RRR)**\n"
        f"• **Entry Zone:** {entry_zone}\n"
        f"• **Stop Loss (SL):** ₹{sl:,.2f} (Risk: {sl_dist} pts)\n"
        f"• **Take Profit (TP):** ₹{tp:,.2f} (Reward: {tp_dist} pts)\n\n"
        f"📋 **Indicators:** VWAP = ₹{vwap} | 5 EMA = ₹{ema_5} | 9 EMA = ₹{ema_9}"
    )

    return report

# ==========================================
# 5. ALL-SEGMENT BACKGROUND AUTO-SCANNER
# ==========================================
def background_all_segment_scanner():
    """Continuously scans all 7 assets every 60 seconds and fires alerts when signals form"""
    logging.info("🚀 Background Auto-Scanner Engine Activated 24/7!")
    time.sleep(10)
    while True:
        try:
            for asset in ASSET_CONFIG:
                alert_text = analyze_asset_scalp(asset, is_auto_scan=True)
                if alert_text:
                    logging.info(f"⚡ New Signal Triggered for {asset}! Dispatching Telegram Alert...")
                    send_telegram_alert(alert_text)
                time.sleep(2)  # Short delay between asset checks
        except Exception as e:
            logging.error(f"⚠️ Scanner Loop Error: {e}")
        time.sleep(60)  # Scan every 1 minute

# ==========================================
# 6. RENDER WEB SERVER
# ==========================================
app_flask = Flask(__name__)

@app_flask.route("/")
def home():
    return "🚀 Emerald Trade Agent Live & Scanning All Segments 24/7!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app_flask.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# ==========================================
# 7. TELEGRAM HANDLERS
# ==========================================
def get_main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 NIFTY", callback_data="ANALYZE_NIFTY"), InlineKeyboardButton("🏦 BANK NIFTY", callback_data="ANALYZE_BANK NIFTY")],
        [InlineKeyboardButton("📊 SENSEX", callback_data="ANALYZE_SENSEX"), InlineKeyboardButton("🛢️ CRUDE OIL", callback_data="ANALYZE_CRUDE OIL")],
        [InlineKeyboardButton("🔥 NATURAL GAS", callback_data="ANALYZE_NATURAL GAS"), InlineKeyboardButton("🥇 GOLD", callback_data="ANALYZE_GOLD")],
        [InlineKeyboardButton("🥈 SILVER", callback_data="ANALYZE_SILVER")],
    ])

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "🚀 *Institutional Trading Engine Online & Auto-Scanning!*\n\n"
        "⚡ The bot is now continuously scanning **NIFTY, BANK NIFTY, SENSEX, CRUDE OIL, NATURAL GAS, GOLD, and SILVER** in the background.\n\n"
        "🔔 You will receive instant push notification alerts here whenever a breakout or trade signal forms!\n\n"
        "Or tap any button below for an instant manual analysis:"
    )
    await update.message.reply_text(welcome_text, reply_markup=get_main_keyboard(), parse_mode="Markdown")

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("ANALYZE_"):
        asset = query.data.replace("ANALYZE_", "")
        report = analyze_asset_scalp(asset, is_auto_scan=False)
        await context.bot.send_message(chat_id=query.message.chat_id, text=report, parse_mode="Markdown", reply_markup=get_main_keyboard())

# ==========================================
# 8. MAIN EXECUTION ENTRYPOINT
# ==========================================
if __name__ == "__main__":
    initialize_fyers()

    # Start Flask Web Server
    threading.Thread(target=run_flask, daemon=True).start()

    # Start Background Auto-Scanner for All Segments
    threading.Thread(target=background_all_segment_scanner, daemon=True).start()

    # Build and Run Telegram Bot
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_callback_handler))

    logging.info("✅ Starting Telegram Bot...")
    app.run_polling(drop_pending_updates=True)
