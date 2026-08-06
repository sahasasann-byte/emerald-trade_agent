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

ASSET_CONFIG = {
    "NIFTY": {"fyers": "NSE:NIFTY50-INDEX", "yahoo": "^NSEI", "step": 50, "unit": "pts"},
    "BANK NIFTY": {"fyers": "NSE:NIFTYBANK-INDEX", "yahoo": "^NSEBANK", "step": 100, "unit": "pts"},
    "SENSEX": {"fyers": "BSE:SENSEX-INDEX", "yahoo": "^BSESN", "step": 100, "unit": "pts"},
    "CRUDE OIL": {"fyers": "MCX:CRUDEOIL26AUGFUT", "yahoo": "CL=F", "step": 10, "unit": "₹/bbl", "is_commodity": True},
    "NATURAL GAS": {"fyers": "MCX:NATURALGAS26AUGFUT", "yahoo": "NG=F", "step": 1, "unit": "₹/mmBtu", "is_commodity": True},
    "GOLD": {"fyers": "MCX:GOLD26OCTFUT", "yahoo": "GC=F", "step": 100, "unit": "₹/10g", "is_commodity": True},
    "SILVER": {"fyers": "MCX:SILVER26SEPFUT", "yahoo": "SI=F", "step": 100, "unit": "₹/kg", "is_commodity": True},
}

fyers = None
LAST_SIGNAL_STATE = {asset: None for asset in ASSET_CONFIG}

def initialize_fyers():
    global fyers
    if not FYERS_AVAILABLE:
        logging.warning("⚠️ Fyers SDK not available.")
        return
    token = os.environ.get("FYERS_ACCESS_TOKEN", "")
    if token:
        try:
            fyers = fyersModel.FyersModel(client_id=FYERS_CLIENT_ID, is_async=False, token=token, log_path="")
            logging.info("✅ Fyers API Initialized Successfully!")
        except Exception as e:
            logging.error(f"❌ Fyers Init Failed: {e}")

# ==========================================
# 2. FYERS DATA HEALTH CHECK ENGINE
# ==========================================
def check_fyers_data_health():
    token = os.environ.get("FYERS_ACCESS_TOKEN", "")
    if not token:
        return "⚠️ **FYERS API STATUS:** Token variable not set in Render Environment."
    if not fyers:
        return "❌ **FYERS API STATUS:** Not Initialized / Invalid SDK Setup"

    try:
        profile = fyers.get_profile()
        if profile and profile.get("s") == "ok":
            user_data = profile.get("data", {})
            user_name = user_data.get("name", "Fyers Trader")
            fy_id = user_data.get("fy_id", "FAK37502")
            return (
                f"✅ **FYERS API STATUS: ONLINE & OK** 🟢\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"• **Account ID:** `{fy_id}` ({user_name})\n"
                f"• **Status:** Active Live Connection\n"
                f"• **Real-Time Data:** Operating Direct Fyers Feed"
            )
        else:
            msg = profile.get('message', 'Invalid Token') if profile else 'No Response'
            return (
                f"❌ **FYERS ACCESS TOKEN EXPIRED / INVALID** 🔴\n\n"
                f"Fyers error response: `{msg}`\n\n"
                f"💡 *The bot is automatically running using Live Web Fallback Data.*"
            )
    except Exception as e:
        return f"⚠️ **Fyers Status Exception:** `{str(e)}`"

# ==========================================
# 3. TELEGRAM ALERT DISPATCHER
# ==========================================
def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logging.error(f"⚠️ Telegram Dispatch Error: {e}")

# ==========================================
# 4. DATA ENGINE (INR & FALLBACK)
# ==========================================
def fetch_usd_inr_rate():
    try:
        url = "https://query1.finance.yahoo.com/v8/finance/chart/USDINR=X?range=1d&interval=1m"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=4)
        if res.status_code == 200:
            return float(res.json()["chart"]["result"][0]["meta"]["regularMarketPrice"])
    except Exception:
        pass
    return 83.50

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
            if res and res.get("s") == "ok" and res.get("candles"):
                df = pd.DataFrame(res["candles"], columns=["timestamp", "open", "high", "low", "close", "volume"])
                df["time"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
                return df
        except Exception:
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

            if config.get("is_commodity"):
                usd_inr = fetch_usd_inr_rate()
                mult = usd_inr
                if asset_name == "GOLD":
                    mult = (usd_inr / 31.1035) * 10
                elif asset_name == "SILVER":
                    mult = (usd_inr / 31.1035) * 1000
                df["open"] *= mult
                df["high"] *= mult
                df["low"] *= mult
                df["close"] *= mult

            df["time"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
            return df
    except Exception as e:
        logging.error(f"❌ Fallback fetch failed for {asset_name}: {e}")

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
# 5. ANALYSIS ENGINE
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

    config = ASSET_CONFIG[asset_name]
    step = config["step"]
    unit = config["unit"]
    atm_strike = round(curr_price / step) * step

    sl_dist = max(round(atr * 1.2, 2), step * 0.4)
    tp_dist = round(sl_dist * 1.9, 2)

    current_signal = "NEUTRAL"

    if curr_price > vwap and ema_5 > ema_9:
        current_signal = "BUY"
        ce_strike = int(atm_strike - step if asset_name in ["NIFTY", "BANK NIFTY", "SENSEX"] else atm_strike)
        option_pick = f"`{ce_strike} CALL (CE)`"
        entry_zone = f"₹{curr_price - (step*0.05):,.2f} - ₹{curr_price + (step*0.05):,.2f}"
        sl = round(curr_price - sl_dist, 2)
        tp = round(curr_price + tp_dist, 2)
        signal_header = "🚨 **NEW INSTITUTIONAL SIGNAL: BUY** 🟢"
        bias_desc = f"Bullish breakout above VWAP with 5/9 EMA crossover."
    elif curr_price < vwap and ema_5 < ema_9:
        current_signal = "SELL"
        pe_strike = int(atm_strike + step if asset_name in ["NIFTY", "BANK NIFTY", "SENSEX"] else atm_strike)
        option_pick = f"`{pe_strike} PUT (PE)`"
        entry_zone = f"₹{curr_price - (step*0.05):,.2f} - ₹{curr_price + (step*0.05):,.2f}"
        sl = round(curr_price + sl_dist, 2)
        tp = round(curr_price - tp_dist, 2)
        signal_header = "🚨 **NEW INSTITUTIONAL SIGNAL: SELL** 🔴"
        bias_desc = f"Bearish breakdown below VWAP with 5/9 EMA crossover."
    else:
        if is_auto_scan:
            LAST_SIGNAL_STATE[asset_name] = "NEUTRAL"
            return None
        return (
            f"⚡ **LIVE MARKET ANALYSIS: {asset_name}**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"• Current Spot Price: **₹{curr_price:,.2f} {unit}**\n"
            f"• Live VWAP: **₹{vwap:,.2f}** | 5 EMA: **₹{ema_5:,.2f}**\n"
            f"• Market Status: **NO TRADE ZONE (Consolidation)**\n\n"
            f"💡 *Spot price is trapped near VWAP. Awaiting breakout.*"
        )

    if is_auto_scan:
        if LAST_SIGNAL_STATE[asset_name] == current_signal:
            return None
        LAST_SIGNAL_STATE[asset_name] = current_signal

    report = (
        f"{signal_header}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"• **Asset:** `{asset_name}`\n"
        f"• **Option Strike:** {option_pick}\n"
        f"• **Live INR Price:** ₹{curr_price:,.2f} {unit}\n"
        f"• **Order Flow Bias:** {bias_desc}\n\n"
        f"🎯 **TARGETS & RISK MANAGEMENT (1:1.9 RRR)**\n"
        f"• **Entry Zone:** {entry_zone}\n"
        f"• **Stop Loss (SL):** ₹{sl:,.2f} (Risk: {sl_dist} pts)\n"
        f"• **Take Profit (TP):** ₹{tp:,.2f} (Reward: {tp_dist} pts)\n"
        f"• **Risk-Reward Ratio:** **1 : 1.9**\n\n"
        f"📋 **Indicators:** VWAP = ₹{vwap} | 5 EMA = ₹{ema_5} | 9 EMA = ₹{ema_9}"
    )

    return report

# ==========================================
# 6. BACKGROUND AUTO SCANNER
# ==========================================
def background_all_segment_scanner():
    logging.info("🚀 Background Auto-Scanner Engine Active 24/7!")
    time.sleep(10)
    while True:
        try:
            for asset in ASSET_CONFIG:
                alert_text = analyze_asset_scalp(asset, is_auto_scan=True)
                if alert_text:
                    send_telegram_alert(alert_text)
                time.sleep(2)
        except Exception as e:
            logging.error(f"⚠️ Scanner Error: {e}")
        time.sleep(60)

# ==========================================
# 7. RENDER WEB SERVER
# ==========================================
app_flask = Flask(__name__)

@app_flask.route("/")
def home():
    return "🚀 Emerald Trade Agent Live with Fyers & INR Commodity Engine!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app_flask.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# ==========================================
# 8. TELEGRAM HANDLERS
# ==========================================
def get_main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 NIFTY", callback_data="ANALYZE_NIFTY"), InlineKeyboardButton("🏦 BANK NIFTY", callback_data="ANALYZE_BANK NIFTY")],
        [InlineKeyboardButton("📊 SENSEX", callback_data="ANALYZE_SENSEX"), InlineKeyboardButton("🛢️ CRUDE OIL", callback_data="ANALYZE_CRUDE OIL")],
        [InlineKeyboardButton("🔥 NATURAL GAS", callback_data="ANALYZE_NATURAL GAS"), InlineKeyboardButton("🥇 GOLD", callback_data="ANALYZE_GOLD")],
        [InlineKeyboardButton("🥈 SILVER", callback_data="ANALYZE_SILVER")],
        [InlineKeyboardButton("🔍 CHECK FYERS API DATA", callback_data="CHECK_FYERS")]
    ])

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "🚀 *Institutional Trading Engine Online!*\n\n"
        "⚡ Continuous auto-scanning active for **NIFTY, BANK NIFTY, SENSEX, CRUDE OIL, NATURAL GAS, GOLD, & SILVER**.\n"
        "💰 *All Commodity Prices are calculated and displayed in INR (₹).*\n\n"
        "Tap any button below for live signals or to verify Fyers connection status:"
    )
    await update.message.reply_text(welcome_text, reply_markup=get_main_keyboard(), parse_mode="Markdown")

async def check_fyers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    status_text = check_fyers_data_health()
    await update.message.reply_text(status_text, parse_mode="Markdown")

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "CHECK_FYERS":
        status_text = check_fyers_data_health()
        await context.bot.send_message(chat_id=query.message.chat_id, text=status_text, parse_mode="Markdown", reply_markup=get_main_keyboard())
    elif query.data.startswith("ANALYZE_"):
        asset = query.data.replace("ANALYZE_", "")
        report = analyze_asset_scalp(asset, is_auto_scan=False)
        await context.bot.send_message(chat_id=query.message.chat_id, text=report, parse_mode="Markdown", reply_markup=get_main_keyboard())

# ==========================================
# 9. MAIN EXECUTION
# ==========================================
if __name__ == "__main__":
    initialize_fyers()

    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=background_all_segment_scanner, daemon=True).start()

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("check_fyers", check_fyers_command))
    app.add_handler(CallbackQueryHandler(button_callback_handler))

    logging.info("✅ Starting Telegram Bot...")
    app.run_polling(drop_pending_updates=True)
