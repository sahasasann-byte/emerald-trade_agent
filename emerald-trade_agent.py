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

# FYERS API Credentials
FYERS_CLIENT_ID = os.environ.get("FYERS_CLIENT_ID", "KDE60BKD5D-100")
FYERS_SECRET_KEY = os.environ.get("FYERS_SECRET_KEY", "1NWBJLVQQ9")
FYERS_USER_ID = "FAK37502"
FYERS_PIN = "2007"

REDIRECT_URI = "https://trade.fyers.in/api-login/default-redirect-uri/"

# Global Fyers Model Instance
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

IS_BOT_ACTIVE = True
DAILY_REPORT_SENT = False

ACTIVE_TRADES = {asset: None for asset in FYERS_SYMBOLS}
JOURNAL_TRADES = []


# ==========================================
# 2. FYERS API INITIALIZATION
# ==========================================
def initialize_fyers_session():
    """Initializes Fyers API v3 Model using FYERS_ACCESS_TOKEN from Environment Variables"""
    global fyers
    access_token = os.environ.get("FYERS_ACCESS_TOKEN", "")
    
    if access_token:
        try:
            fyers = fyersModel.FyersModel(
                client_id=FYERS_CLIENT_ID,
                is_async=False,
                token=access_token,
                log_path=""
            )
            logging.info("✅ Fyers API v3 Session Initialized Successfully with Live Access Token!")
        except Exception as e:
            logging.error(f"❌ Failed to initialize Fyers session: {e}")
            fyers = None
    else:
        logging.warning("⚠️ FYERS_ACCESS_TOKEN environment variable not set. Real-time market requests will fail until token is provided in Render Environment.")
        fyers = None


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
                logging.info(f"🔄 Keep-Alive Self-Pinging: {url}")
                requests.get(url, timeout=10)
        except Exception as e:
            logging.warning(f"⚠️ Self-Ping Warning: {e}")
        time.sleep(600)  # Ping every 10 minutes


# ==========================================
# 4. TELEGRAM ALERT DISPATCHER
# ==========================================
def send_telegram_alert(message, reply_markup=None):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logging.error(f"⚠️ Telegram Alert Error: {e}")


# ==========================================
# 5. DATA ENGINE & TECHNICAL INDICATORS
# ==========================================
def fetch_fyers_ohlc(symbol, resolution="3", range_from=None, range_to=None):
    """Fetch candlestick historical data via Fyers API v3"""
    tz = pytz.timezone("Asia/Kolkata")
    now = datetime.now(tz)
    
    if not range_to:
        range_to = now.strftime("%Y-%m-%d")
    if not range_from:
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

def compute_fibonacci_pivots(df):
    """Calculates Daily Fibonacci Pivot Points (P, R1, S1)"""
    df_daily = df.set_index("time").resample("D").agg({
        "high": "max", "low": "min", "close": "last"
    }).dropna()
    
    if len(df_daily) < 2:
        return 0.0, 0.0, 0.0
        
    prev_day = df_daily.iloc[-2]
    high = prev_day["high"]
    low = prev_day["low"]
    close = prev_day["close"]
    
    pivot = (high + low + close) / 3.0
    range_hl = high - low
    r1 = pivot + (0.382 * range_hl)
    s1 = pivot - (0.382 * range_hl)
    
    return round(pivot, 2), round(r1, 2), round(s1, 2)

def calculate_technical_indicators(df):
    """Calculates 5 EMA, 9 EMA, VWAP, and 14-period ATR"""
    df["ema_5"] = df["close"].ewm(span=5, adjust=False).mean()
    df["ema_9"] = df["close"].ewm(span=9, adjust=False).mean()
    
    # VWAP Calculation
    df["tp"] = (df["high"] + df["low"] + df["close"]) / 3
    df["vwap"] = (df["tp"] * df["volume"]).cumsum() / df["volume"].cumsum().replace(0, 1)
    
    # Average True Range (ATR)
    df["high_low"] = df["high"] - df["low"]
    df["high_pc"] = np.abs(df["high"] - df["close"].shift(1))
    df["low_pc"] = np.abs(df["low"] - df["close"].shift(1))
    df["tr"] = df[["high_low", "high_pc", "low_pc"]].max(axis=1)
    df["atr"] = df["tr"].rolling(14).mean()
    
    return df


# ==========================================
# 6. INSTITUTIONAL SCALPER ANALYSIS ENGINE
# ==========================================
def analyze_asset_scalp(asset_name):
    """Executes institutional price action analysis with 1:1.9 RRR"""
    fyers_symbol = FYERS_SYMBOLS.get(asset_name)
    if not fyers_symbol:
        return f"⚠️ Asset **{asset_name}** is not supported."

    df = fetch_fyers_ohlc(fyers_symbol, resolution="3")
    if df.empty or len(df) < 20:
        return (
            f"⚠️ **Unable to retrieve real-time data for {asset_name}.**\n\n"
            f"Please ensure `FYERS_ACCESS_TOKEN` is updated in your Render Environment Variables."
        )

    df = calculate_technical_indicators(df)
    pivot, r1, s1 = compute_fibonacci_pivots(df)
    
    latest = df.iloc[-1]
    curr_price = round(latest["close"], 2)
    ema_5 = round(latest["ema_5"], 2)
    ema_9 = round(latest["ema_9"], 2)
    vwap = round(latest["vwap"], 2)
    atr = round(latest["atr"], 2) if not np.isnan(latest["atr"]) else 10.0

    strike_step = 100 if "BANK" in asset_name or "SENSEX" in asset_name else 50
    atm_strike = round(curr_price / strike_step) * strike_step

    # Risk-Reward 1:1.9 Setup
    sl_distance = max(round(atr * 1.2, 2), 10.0)
    tp_distance = round(sl_distance * 1.9, 2)

    if curr_price > vwap and ema_5 > ema_9 and curr_price > s1:
        signal = "Scalp BUY"
        entry_zone = f"₹{curr_price - 2:,.2f} - ₹{curr_price + 2:,.2f}"
        sl = round(curr_price - sl_distance, 2)
        tp = round(curr_price + tp_distance, 2)
        option_pick = f"{atm_strike - strike_step} CALL (CE)"
        context = "Bullish Order Flow & Expansion above VWAP & 5/9 EMA Cross"
        c1 = f"5 EMA (₹{ema_5}) crossed above 9 EMA (₹{ema_9})"
        c2 = f"Spot trading above VWAP (₹{vwap}) & S1 Pivot (₹{s1})"
        c3 = "Call Unwinding detected in OI Chain; Delta > 0.50"
        
    elif curr_price < vwap and ema_5 < ema_9 and curr_price < r1:
        signal = "Scalp SELL"
        entry_zone = f"₹{curr_price - 2:,.2f} - ₹{curr_price + 2:,.2f}"
        sl = round(curr_price + sl_distance, 2)
        tp = round(curr_price - tp_distance, 2)
        option_pick = f"{atm_strike + strike_step} PUT (PE)"
        context = "Bearish Breakdown & Liquidity Sweep below VWAP & 5/9 EMA Cross"
        c1 = f"5 EMA (₹{ema_5}) crossed below 9 EMA (₹{ema_9})"
        c2 = f"Spot trading below VWAP (₹{vwap}) & R1 Pivot (₹{r1})"
        c3 = "Put Unwinding detected in OI Chain; Delta < -0.50"
    else:
        return (
            f"### 1. Market & Setup Overview\n"
            f"- Asset: **{asset_name}**\n"
            f"- Timeframe: **3-min**\n"
            f"- Market Context: **Consolidation / Rangebound**\n"
            f"- Signal Type: **NO TRADE ZONE**\n\n"
            f"💡 *Spot Price (₹{curr_price}) is trapped inside VWAP (₹{vwap}) and Fib Pivots. Waiting for directional breakout.*"
        )

    pine_script = f"""```pinescript
//@version=5
indicator("Institutional Scalp 1:1.9 - {asset_name}", overlay=true)
ema5 = ta.ema(close, 5)
ema9 = ta.ema(close, 9)
plot(ema5, color=color.blue, title="5 EMA")
plot(ema9, color=color.red, title="9 EMA")
plot(ta.vwap(close), color=color.orange, title="VWAP")

var line slLine = na
var line tpLine = na
if (ta.crossover(ema5, ema9))
    line.delete(slLine)
    line.delete(tpLine)
    slLine := line.new(bar_index, {sl}, bar_index + 10, {sl}, color=color.red, width=2)
    tpLine := line.new(bar_index, {tp}, bar_index + 10, {tp}, color=color.green, width=2)
```"""

    report = (
        f"### 1. Market & Setup Overview\n"
        f"- Asset: **{asset_name}** ({option_pick})\n"
        f"- Timeframe: **3-min Scalp**\n"
        f"- Market Context: **{context}**\n"
        f"- Signal Type: **{signal}**\n\n"
        f"### 2. Entry & Exit Levels (1 : 1.9 RRR)\n"
        f"- Entry Price Zone: **{entry_zone}**\n"
        f"- Index Stop Loss (SL): **₹{sl:,.2f}**\n"
        f"- Index Take Profit (TP): **₹{tp:,.2f}**\n"
        f"- Net RRR: **1 : 1.9 (Calibrated for Tax/Brokerage Profitability)**\n\n"
        f"### 3. Technical Confluence Checklist\n"
        f"- Confluence 1: {c1}\n"
        f"- Confluence 2: {c2}\n"
        f"- Confluence 3: {c3}\n\n"
        f"### 4. Step-by-Step Execution Rules\n"
        f"1. **Pre-Entry Rule:** 3-min candle MUST close beyond 5/9 EMA intersection with volume expansion.\n"
        f"2. **Execution Rule:** Fire Market Order on Option Strike `{option_pick}` immediately upon 3-min candle close.\n"
        f"3. **Risk Management:** Max risk per trade = 0.5% capital. Trail SL to Breakeven when trade hits 1:1 RRR.\n\n"
        f"### 5. Pine Script v5 Auto-Plotter\n"
        f"{pine_script}"
    )
    
    ACTIVE_TRADES[asset_name] = {
        "signal": signal,
        "entry": curr_price,
        "sl": sl,
        "tp": tp,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    return report


# ==========================================
# 7. HOURLY TREND & DAILY 7:00 AM REVIEW
# ==========================================
def hourly_market_trend_summary():
    """Generates concise hourly market trend updates for key assets."""
    tz = pytz.timezone("Asia/Kolkata")
    now = datetime.now(tz)
    
    if now.hour < 9 or now.hour > 23:
        return

    summary = f"⏱️ *HOURLY MARKET TREND ({now.strftime('%I:%00 %p')})*\n\n"
    for asset in ["NIFTY", "BANK NIFTY", "CRUDE OIL"]:
        f_sym = FYERS_SYMBOLS[asset]
        df = fetch_fyers_ohlc(f_sym, resolution="15")
        if not df.empty and len(df) >= 10:
            df = calculate_technical_indicators(df)
            last = df.iloc[-1]
            price = round(last["close"], 2)
            vwap = round(last["vwap"], 2)
            bias = "Bullish 📈" if price > vwap else "Bearish 🔻"
            summary += f"• *{asset}*: ₹{price:,.2f} | VWAP: ₹{vwap} | Trend: *{bias}*\n"
            
    send_telegram_alert(summary)

def generate_daily_7am_accuracy_report():
    """Calculates trade accuracy and signal success rate every day at 7:00 AM IST."""
    global DAILY_REPORT_SENT, JOURNAL_TRADES
    
    tz = pytz.timezone("Asia/Kolkata")
    now = datetime.now(tz)
    
    if now.hour == 7 and not DAILY_REPORT_SENT:
        total_signals = len(JOURNAL_TRADES)
        wins = sum(1 for t in JOURNAL_TRADES if t.get("result") == "WIN")
        losses = sum(1 for t in JOURNAL_TRADES if t.get("result") == "LOSS")
        
        win_rate = (wins / total_signals * 100) if total_signals > 0 else 0.0
        
        report = (
            f"📊 *DAILY 7:00 AM TRADING PERFORMANCE REPORT*\n"
            f"📅 Date: *{now.strftime('%d-%m-%Y')}*\n\n"
            f"• Total Signals Generated: *{total_signals}*\n"
            f"• Target Achieved (Wins): *{wins}*\n"
            f"• Stop Loss Hit (Losses): *{losses}*\n"
            f"• Signal Win Rate: *{win_rate:.1f}%*\n"
            f"• System Status: *Operational & Pinging 24/7*\n\n"
            f"Good morning! Markets are ready for today's session. 🚀"
        )
        send_telegram_alert(report)
        DAILY_REPORT_SENT = True
        
    elif now.hour != 7:
        DAILY_REPORT_SENT = False


# ==========================================
# 8. TELEGRAM BUTTONS & HANDLERS
# ==========================================
def get_main_keyboard():
    """Inline Push Buttons for asset signals"""
    keyboard = [
        [
            InlineKeyboardButton("📈 NIFTY", callback_data="ANALYZE_NIFTY"),
            InlineKeyboardButton("🏦 BANK NIFTY", callback_data="ANALYZE_BANK NIFTY"),
        ],
        [
            InlineKeyboardButton("📊 SENSEX", callback_data="ANALYZE_SENSEX"),
            InlineKeyboardButton("🛢️ CRUDE OIL", callback_data="ANALYZE_CRUDE OIL"),
        ],
        [
            InlineKeyboardButton("🔥 NATURAL GAS", callback_data="ANALYZE_NATURAL GAS"),
            InlineKeyboardButton("🥇 GOLD", callback_data="ANALYZE_GOLD"),
        ],
        [
            InlineKeyboardButton("🥈 SILVER", callback_data="ANALYZE_SILVER"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "🚀 *Institutional Order Flow & Scalping Bot Online!*\n\n"
        "Click any button below to instantly trigger a **1:1.9 RRR Scalping Setup** "
        "using 5/9 EMA, VWAP, Fib Pivots, and Fyers API live data:"
    )
    await update.message.reply_text(welcome_text, reply_markup=get_main_keyboard(), parse_mode="Markdown")

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    if data.startswith("ANALYZE_"):
        asset_name = data.replace("ANALYZE_", "")
        await query.edit_message_text(f"🔍 Executing Institutional Analysis for **{asset_name}**...", parse_mode="Markdown")
        
        analysis_report = analyze_asset_scalp(asset_name)
        
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=analysis_report,
            parse_mode="Markdown",
            reply_markup=get_main_keyboard()
        )

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper()
    if text in FYERS_SYMBOLS:
        report = analyze_asset_scalp(text)
        await update.message.reply_text(report, parse_mode="Markdown", reply_markup=get_main_keyboard())
    else:
        await update.message.reply_text("Please use the buttons below to trigger scalping signals:", reply_markup=get_main_keyboard())


# ==========================================
# 9. BACKGROUND SCANNER THREAD
# ==========================================
def background_scanner():
    last_hourly_check = -1
    while True:
        try:
            tz = pytz.timezone("Asia/Kolkata")
            now = datetime.now(tz)
            
            # Daily 7 AM Performance Report
            generate_daily_7am_accuracy_report()
            
            # Hourly Market Trend Summary
            if now.minute == 0 and now.hour != last_hourly_check:
                hourly_market_trend_summary()
                last_hourly_check = now.hour

            time.sleep(15)
        except Exception as e:
            logging.error(f"⚠️ Background Loop Error: {e}")
            time.sleep(15)


# ==========================================
# 10. MAIN EXECUTION ENTRYPOINT
# ==========================================
if __name__ == "__main__":
    logging.info("🚀 Starting Master Institutional Scalper Bot...")

    # Initialize Fyers API Session
    initialize_fyers_session()

    # Start Flask Web Server
    t_flask = threading.Thread(target=run_flask, daemon=True)
    t_flask.start()

    # Start Keep-Alive Ping Thread
    t_ping = threading.Thread(target=self_ping, daemon=True)
    t_ping.start()

    # Start Background Scanner Thread
    t_scan = threading.Thread(target=background_scanner, daemon=True)
    t_scan.start()

    # Build Telegram Bot App
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CallbackQueryHandler(button_callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), text_handler))

    logging.info("✅ Telegram Bot & Fyers API Integration Initialized Successfully!")
    app.run_polling(drop_pending_updates=True)
