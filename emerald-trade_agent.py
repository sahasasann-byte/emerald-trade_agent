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
from fyers_apiv3 import fyersModel

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

# Logging Setup
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

# ==========================================
# 1. CREDENTIALS & CONFIGURATION
# ==========================================
TELEGRAM_BOT_TOKEN = "8866649004:AAHuRrhqCHqRq0Ucb1i_UyTCG2B5nKOCkps"
TELEGRAM_CHAT_ID = "5944911045"

# FYERS API Credentials
FYERS_CLIENT_ID = "KDE60BKD5D-100"
FYERS_SECRET_KEY = "1NWBJLVQQ9"
FYERS_USER_ID = "FAK37502"
FYERS_PIN = "2007"

# YOUR GENERATED FYERS ACCESS TOKEN
HARDCODED_ACCESS_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJhdWQiOlsiZDoxIiwiZDoyIiwieDowIiwieDoxIl0sImF0X2hhc2giOiJnQUFBQUFCcWNoRG5hZ3B6blBPLWpmYkVLYzFtdFhZcmszWnFSYTVGYXZLS0xQY2xYUlYzTnpTc2JxTzR5WTNZR3E2cHduNm1rU0J4VEJDRDAyVHlUd1lkZU1uaDkwWEVBVDRuYlEzbWNXU2UzRzhCTGZLb3RuRT0iLCJkaXNwbGF5X25hbWUiOiIiLCJvbXMiOiJLMSIsImhzbV9rZXkiOiJhZGFkMzlhZDQwOWUxZTcwNjU5ZDdiNDI4N2ZiNGFiZjE5YzlmN2ZkOGYwMzhjMDIwYzdhYzNiNCIsImlzRGRwaUVuYWJsZWQiOiJOIiwiaXNNdGZFbmFibGVkIjoiTiIsImZ5X2lkIjoiRkFLMzc1MDIiLCJhcHBUeXBlIjoxMDAsImV4cCI6MTc4NTg4OTgwMCwiaWF0IjoxNzg1ODYwMzI3LCJpc3MiOiJhcGkuZnllcnMuaW4iLCJuYmYiOjE3ODU4NjAzMjcsInN1YiI6ImFjY2Vzc190b2tlbiJ9.aFqvqHBsMSNHdMK4xANDBx2I2lUbPPSqCWzkQyIkIdA"

REDIRECT_URI = "https://trade.fyers.in/api-login/default-redirect-uri/"

# Global Fyers Instance
fyers = None

# Asset Mapping for Fyers Symbols
FYERS_SYMBOLS = {
    "NIFTY": "NSE:NIFTY50-INDEX",
    "BANK NIFTY": "NSE:NIFTYBANK-INDEX",
    "SENSEX": "BSE:SENSEX-INDEX",
    "CRUDE OIL": "MCX:CRUDEOIL26AUGFUT",
    "NATURAL GAS": "MCX:NATURALGAS26AUGFUT",
    "GOLD": "MCX:GOLD26OCTFUT",
    "SILVER": "MCX:SILVER26SEPFUT",
    "INDIA VIX": "NSE:INDIAVIX-INDEX"
}

ACTIVE_TRADES = {asset: None for asset in FYERS_SYMBOLS}
JOURNAL_TRADES = []


# ==========================================
# 2. FYERS API INITIALIZATION
# ==========================================
def initialize_fyers_session():
    """Initializes Fyers API v3 Model using environment variable or fallback token"""
    global fyers
    access_token = os.environ.get("FYERS_ACCESS_TOKEN", HARDCODED_ACCESS_TOKEN)
    
    if access_token:
        try:
            fyers = fyersModel.FyersModel(
                client_id=FYERS_CLIENT_ID,
                is_async=False,
                token=access_token,
                log_path=""
            )
            logging.info("✅ Fyers API v3 Session Initialized Successfully!")
        except Exception as e:
            logging.error(f"❌ Failed to initialize Fyers session: {e}")
            fyers = None
    else:
        logging.warning("⚠️ No access token found.")


# ==========================================
# 3. RENDER WEB SERVER & KEEP-ALIVE
# ==========================================
app_flask = Flask(__name__)
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "http://127.0.0.1:8080")

@app_flask.route("/")
def home():
    return "🚀 Institutional Order Flow & Scalping Engine Active 24/7!"

@app_flask.route("/health")
def health():
    return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app_flask.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

def self_ping():
    time.sleep(30)
    while True:
        try:
            url = RENDER_EXTERNAL_URL
            if "127.0.0.1" not in url:
                requests.get(url, timeout=10)
        except Exception:
            pass
        time.sleep(600)


# ==========================================
# 4. DATA ENGINE & TECHNICAL INDICATORS
# ==========================================
def fetch_fyers_ohlc(symbol, resolution="3"):
    tz = pytz.timezone("Asia/Kolkata")
    now = datetime.now(tz)
    range_to = now.strftime("%Y-%m-%d")
    range_from = (now - timedelta(days=3)).strftime("%Y-%m-%d")
        
    data = {
        "symbol": symbol,
        "resolution": resolution,
        "date_format": "1",
        "range_from": range_from,
        "range_to": range_to,
        "cont_flag": "1"
    }
    try:
        if fyers:
            response = fyers.history(data=data)
            if response.get("s") == "ok":
                candles = response.get("candles")
                df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df["time"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
                return df
    except Exception as e:
        logging.error(f"❌ Error fetching Fyers data for {symbol}: {e}")
    return pd.DataFrame()

def calculate_technical_indicators(df):
    df["ema_5"] = df["close"].ewm(span=5, adjust=False).mean()
    df["ema_9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["tp"] = (df["high"] + df["low"] + df["close"]) / 3
    df["vwap"] = (df["tp"] * df["volume"]).cumsum() / df["volume"].cumsum().replace(0, 1)
    df["high_low"] = df["high"] - df["low"]
    df["high_pc"] = np.abs(df["high"] - df["close"].shift(1))
    df["low_pc"] = np.abs(df["low"] - df["close"].shift(1))
    df["tr"] = df[["high_low", "high_pc", "low_pc"]].max(axis=1)
    df["atr"] = df["tr"].rolling(14).mean()
    return df

def analyze_asset_scalp(asset_name):
    fyers_symbol = FYERS_SYMBOLS.get(asset_name)
    if not fyers_symbol:
        return f"⚠️ Asset **{asset_name}** is not supported."

    df = fetch_fyers_ohlc(fyers_symbol, resolution="3")
    if df.empty or len(df) < 20:
        return f"⚠️ Unable to retrieve real-time data for **{asset_name}**."

    df = calculate_technical_indicators(df)
    latest = df.iloc[-1]
    curr_price = round(latest["close"], 2)
    ema_5 = round(latest["ema_5"], 2)
    ema_9 = round(latest["ema_9"], 2)
    vwap = round(latest["vwap"], 2)
    atr = round(latest["atr"], 2) if not np.isnan(latest["atr"]) else 10.0

    strike_step = 100 if "BANK" in asset_name or "SENSEX" in asset_name else 50
    atm_strike = round(curr_price / strike_step) * strike_step

    sl_distance = max(round(atr * 1.2, 2), 10.0)
    tp_distance = round(sl_distance * 1.9, 2)

    if curr_price > vwap and ema_5 > ema_9:
        signal = "Scalp BUY"
        entry_zone = f"₹{curr_price - 2:,.2f} - ₹{curr_price + 2:,.2f}"
        sl = round(curr_price - sl_distance, 2)
        tp = round(curr_price + tp_distance, 2)
        option_pick = f"{atm_strike - strike_step} CALL (CE)"
        context = "Bullish Order Flow & Expansion above VWAP & 5/9 EMA Cross"
    elif curr_price < vwap and ema_5 < ema_9:
        signal = "Scalp SELL"
        entry_zone = f"₹{curr_price - 2:,.2f} - ₹{curr_price + 2:,.2f}"
        sl = round(curr_price + sl_distance, 2)
        tp = round(curr_price - tp_distance, 2)
        option_pick = f"{atm_strike + strike_step} PUT (PE)"
        context = "Bearish Breakdown & Liquidity Sweep below VWAP & 5/9 EMA Cross"
    else:
        return (
            f"### 1. Market & Setup Overview\n"
            f"- Asset: **{asset_name}**\n"
            f"- Timeframe: **3-min**\n"
            f"- Market Context: **Consolidation / Rangebound**\n"
            f"- Signal Type: **NO TRADE ZONE**\n\n"
            f"💡 *Spot Price (₹{curr_price}) is trapped inside VWAP (₹{vwap}). Waiting for breakout.*"
        )

    return (
        f"### 1. Market & Setup Overview\n"
        f"- Asset: **{asset_name}** ({option_pick})\n"
        f"- Timeframe: **3-min Scalp**\n"
        f"- Market Context: **{context}**\n"
        f"- Signal Type: **{signal}**\n\n"
        f"### 2. Entry & Exit Levels (1 : 1.9 RRR)\n"
        f"- Entry Price Zone: **{entry_zone}**\n"
        f"- Index Stop Loss (SL): **₹{sl:,.2f}**\n"
        f"- Index Take Profit (TP): **₹{tp:,.2f}**\n"
        f"- Net RRR: **1 : 1.9 (Calibrated for Tax/Brokerage Profitability)**\n"
    )


# ==========================================
# 5. TELEGRAM BUTTONS & HANDLERS
# ==========================================
def get_main_keyboard():
    keyboard = [
        [InlineKeyboardButton("📈 NIFTY", callback_data="ANALYZE_NIFTY"), InlineKeyboardButton("🏦 BANK NIFTY", callback_data="ANALYZE_BANK NIFTY")],
        [InlineKeyboardButton("📊 SENSEX", callback_data="ANALYZE_SENSEX"), InlineKeyboardButton("🛢️ CRUDE OIL", callback_data="ANALYZE_CRUDE OIL")],
        [InlineKeyboardButton("🔥 NATURAL GAS", callback_data="ANALYZE_NATURAL GAS"), InlineKeyboardButton("🥇 GOLD", callback_data="ANALYZE_GOLD")],
        [InlineKeyboardButton("🥈 SILVER", callback_data="ANALYZE_SILVER")],
    ]
    return InlineKeyboardMarkup(keyboard)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🚀 *Institutional Scalping Bot Online!*", reply_markup=get_main_keyboard(), parse_mode="Markdown")

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data.startswith("ANALYZE_"):
        asset_name = data.replace("ANALYZE_", "")
        report = analyze_asset_scalp(asset_name)
        await context.bot.send_message(chat_id=query.message.chat_id, text=report, parse_mode="Markdown", reply_markup=get_main_keyboard())


# ==========================================
# 6. MAIN EXECUTION ENTRYPOINT
# ==========================================
if __name__ == "__main__":
    initialize_fyers_session()

    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=self_ping, daemon=True).start()

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_callback_handler))

    logging.info("✅ Telegram Bot & Fyers API Integration Initialized Successfully!")
    app.run_polling(drop_pending_updates=True)
