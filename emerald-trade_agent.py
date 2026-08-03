import asyncio
import os
import sys
import threading
import time
from datetime import datetime
import pytz
import numpy as np
import pandas as pd
import requests
import pyotp
from bs4 import BeautifulSoup
from flask import Flask
from SmartApi import SmartConnect
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ==========================================
# 1. RENDER WEB SERVER & KEEP-ALIVE
# ==========================================
app_flask = Flask(__name__)

RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "http://127.0.0.1:8080")

@app_flask.route("/")
def home():
    return "🚀 Trading Bot Web Server is Active & Running 24/7!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app_flask.run(host="0.0.0.0", port=port)

def self_ping():
    time.sleep(30)
    while True:
        try:
            url = RENDER_EXTERNAL_URL
            print(f"🔄 Keep-Alive Self-Pinging: {url}")
            requests.get(url, timeout=10)
        except Exception as e:
            print(f"⚠️ Self-Ping Warning: {e}")
        time.sleep(600)  # Ping every 10 minutes


# ==========================================
# 2. CONFIGURATION & CREDENTIALS
# ==========================================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8866649004:AAHuRrhqCHqRq0Ucb1i_UyTCG2B5nKOCkps")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "5944911045")

# Angel One Credentials
ANGEL_API_KEY = os.environ.get("ANGEL_API_KEY", "dvQFbsAJ")
ANGEL_CLIENT_CODE = os.environ.get("ANGEL_CLIENT_CODE", "S62895445")
ANGEL_PIN = os.environ.get("ANGEL_PIN", "4482")
TOTP_SECRET = os.environ.get("TOTP_SECRET", "MTLSL363M22F5VIZY574XRFYXU")

IS_BOT_ACTIVE = True
EOD_REPORT_SENT = False

LATEST_DATA = {
    "NIFTY": {"spot": 0.0, "vwap": 0.0, "ema9": 0.0, "ema20": 0.0, "status": "വിശകലനം ചെയ്യുന്നു..."},
    "SENSEX": {"spot": 0.0, "vwap": 0.0, "ema9": 0.0, "ema20": 0.0, "status": "വിശകലനം ചെയ്യുന്നു..."},
}

ACTIVE_TRADES = {
    "NIFTY": None,
    "SENSEX": None,
}

JOURNAL_TRADES = []
smart_api = None
IS_ANGEL_LOGGED_IN = False


# Symbol Mappings for Angel One Tokens
ANGEL_TOKENS = {
    "NIFTY": {"token": "99926000", "exchange": "NSE"},
    "SENSEX": {"token": "99919000", "exchange": "BSE"},
}


# ==========================================
# 3. TIMEZONE-LOCKED MARKET HOURS CHECKER
# ==========================================
def is_market_open():
    """Checks IST (Asia/Kolkata) time regardless of UTC server timezone"""
    tz = pytz.timezone("Asia/Kolkata")
    now = datetime.now(tz)
    
    # Weekend check: Saturday (5), Sunday (6)
    if now.weekday() >= 5:
        return False
        
    market_start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_end = now.replace(hour=15, minute=30, second=0, microsecond=0)
    
    return market_start <= now <= market_end


# ==========================================
# 4. TELEGRAM ALERT DISPATCHER
# ==========================================
def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"⚠️ Telegram Alert Error: {e}")


# ==========================================
# 5. ANGEL ONE LOGIN WITH AUTO-RETRY
# ==========================================
def init_angel_one():
    global smart_api, IS_ANGEL_LOGGED_IN
    try:
        smart_api = SmartConnect(api_key=ANGEL_API_KEY)
        totp = pyotp.TOTP(TOTP_SECRET.strip().replace(" ", "")).now()
        data = smart_api.generateSession(ANGEL_CLIENT_CODE, ANGEL_PIN, totp)
        if data and data.get("status"):
            print("✅ Angel One SmartAPI Logged in Successfully!")
            IS_ANGEL_LOGGED_IN = True
            return True
        else:
            print(f"⚠️ Angel API Login Failed: {data.get('message')}")
            IS_ANGEL_LOGGED_IN = False
    except Exception as e:
        print(f"⚠️ Angel API Login Error: {e}")
        IS_ANGEL_LOGGED_IN = False
    return False


# ==========================================
# 6. DATA FETCHERS (3 FALLBACK SOURCES)
# ==========================================

# --- SOURCE 1: Angel One SmartAPI ---
def fetch_angel_candles(symbol):
    global IS_ANGEL_LOGGED_IN, smart_api
    if not IS_ANGEL_LOGGED_IN:
        if not init_angel_one():
            return pd.DataFrame()

    try:
        token_info = ANGEL_TOKENS.get(symbol.upper())
        if not token_info:
            return pd.DataFrame()

        tz = pytz.timezone("Asia/Kolkata")
        now = datetime.now(tz)
        from_date = now.strftime("%Y-%m-%d 09:15")
        to_date = now.strftime("%Y-%m-%d %H:%M")

        historicParam = {
            "exchange": token_info["exchange"],
            "symboltoken": token_info["token"],
            "interval": "FIVE_MINUTE",
            "fromdate": from_date,
            "todate": to_date,
        }

        response = smart_api.getCandleData(historicParam)
        if response and response.get("status") and response.get("data"):
            candles = response["data"]
            df = pd.DataFrame(candles, columns=["time", "open", "high", "low", "close", "volume"])
            df["time"] = pd.to_datetime(df["time"])
            print(f"✅ Data fetched from PRIMARY Source: Angel One ({symbol})")
            return df
    except Exception as e:
        print(f"⚠️ Angel One Candle Fetch Failed: {e}")
        IS_ANGEL_LOGGED_IN = False  # Trigger re-login on next attempt
    return pd.DataFrame()


# --- SOURCE 2: Yahoo Finance API ---
def fetch_yahoo_candles(ticker, interval="5m", period="1d"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval={interval}&range={period}"
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        res = requests.get(url, headers=headers, timeout=10)
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
                "volume": quotes.get("volume", [1] * len(timestamps)),
            }
        ).dropna()
        print(f"✅ Data fetched from FALLBACK 1 Source: Yahoo Finance ({ticker})")
        return df
    except Exception:
        # Secondary fallback range for Yahoo
        try:
            url_fallback = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval={interval}&range=5d"
            res = requests.get(url_fallback, headers=headers, timeout=10)
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
                    "volume": quotes.get("volume", [1] * len(timestamps)),
                }
            ).dropna()
            print(f"✅ Data fetched from FALLBACK 1 Source: Yahoo Finance 5D ({ticker})")
            return df
        except Exception:
            return pd.DataFrame()


# --- SOURCE 3: Google Finance Scraping ---
def fetch_google_finance_price(symbol):
    ticker_map = {
        "NIFTY": "NIFTY_50:INDEXNSE",
        "SENSEX": "SENSEX:INDEXBO",
    }
    g_symbol = ticker_map.get(symbol.upper(), f"{symbol}:NSE")
    url = f"https://www.google.com/finance/quote/{g_symbol}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    try:
        res = requests.get(url, headers=headers, timeout=10)
        soup = BeautifulSoup(res.text, "html.parser")
        price_div = soup.find("div", {"class": "YMlKec fxfa3d"})
        if price_div:
            price_str = price_div.text.replace("₹", "").replace(",", "").strip()
            curr_price = float(price_str)
            
            # Generate pseudo dataframe for technical calculations if historical candles fail
            times = pd.date_range(end=datetime.now(), periods=20, freq="5min")
            prices = [curr_price] * 20
            df = pd.DataFrame({
                "time": times,
                "open": prices,
                "high": prices,
                "low": prices,
                "close": prices,
                "volume": [1000] * 20
            })
            print(f"✅ Data fetched from FALLBACK 2 Source: Google Finance ({symbol})")
            return df
    except Exception as e:
        print(f"⚠️ Google Finance Scraping Failed: {e}")
    return pd.DataFrame()


# --- MULTI-SOURCE DATA PIPELINE ---
def get_market_data(symbol="NIFTY"):
    df = pd.DataFrame()

    # 1. Primary Source: Angel One
    if symbol in ["NIFTY", "SENSEX"]:
        df = fetch_angel_candles(symbol)

    # 2. Fallback 1: Yahoo Finance
    if df.empty or len(df) < 10:
        ticker = "^NSEI" if symbol == "NIFTY" else ("^BSESN" if symbol == "SENSEX" else f"{symbol}.NS")
        df = fetch_yahoo_candles(ticker)

    # 3. Fallback 2: Google Finance
    if df.empty or len(df) < 10:
        df = fetch_google_finance_price(symbol)

    if df.empty or len(df) < 10:
        print(f"❌ All 3 sources failed to retrieve data for {symbol}.")
        return None

    # Calculate Technical Indicators
    df["ema_9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["ema_20"] = df["close"].ewm(span=20, adjust=False).mean()

    # VWAP Calculation
    df["tp"] = (df["high"] + df["low"] + df["close"]) / 3
    df["vwap"] = (df["tp"] * df["volume"]).cumsum() / df["volume"].cumsum().replace(0, 1)

    return df


# ==========================================
# 7. STOCK SCALP ANALYSIS ENGINE
# ==========================================
def analyze_stock_scalp(stock_symbol):
    clean_symbol = stock_symbol.strip().upper().replace(".NS", "")
    df = get_market_data(clean_symbol)

    if df is None or len(df) < 10:
        return f"⚠️ **{clean_symbol}** എന്ന സ്റ്റോക്കിന്റെ വിവരങ്ങൾ ലഭ്യമായില്ല. Symbol ശരിയാണോ എന്ന് പരിശോധിക്കുക."

    curr_price = round(df["close"].iloc[-1], 2)
    ema_9 = round(df["ema_9"].iloc[-1], 2)
    ema_20 = round(df["ema_20"].iloc[-1], 2)
    vwap = round(df["vwap"].iloc[-1], 2)

    ema_diff_pct = abs(ema_9 - ema_20) / curr_price * 100
    if ema_diff_pct < 0.15:
        return (
            f"📊 *STOCK ANALYSIS: {clean_symbol}*\n\n"
            f"• Current Price: *₹{curr_price:,.2f}*\n"
            f"• VWAP: *₹{vwap}* | 9 EMA: *₹{ema_9}*\n\n"
            f"⚠️ *മാർക്കറ്റ് ചോപ്പിയാണ് (Choppy Market), ഈ സ്റ്റോക്കിൽ ട്രേഡ് ഒഴിവാക്കുന്നു ⏸️*"
        )

    # 1:1.8 Risk Reward Signals
    if curr_price > vwap and ema_9 > ema_20:
        sl = round(ema_20, 2)
        risk = round(curr_price - sl, 2)
        target = round(curr_price + (1.8 * risk), 2)
        return (
            f"🚨 *STOCK BUY SCALP SIGNAL ({clean_symbol})* 🚀\n\n"
            f"• Entry Price: *₹{curr_price:,.2f}*\n"
            f"• Target Price: *₹{target:,.2f}* (1:1.8 Risk-Reward)\n"
            f"• Stop Loss: *₹{sl:,.2f}*\n\n"
            f"💡 *വിശകലനം:* ട്രെൻഡ് ബുള്ളിഷ് ആണ്. പ്രൈസ് VWAP-നും 9 EMA-യ്ക്കും മുകളിലാണ്."
        )

    elif curr_price < vwap and ema_9 < ema_20:
        sl = round(ema_20, 2)
        risk = round(sl - curr_price, 2)
        target = round(curr_price - (1.8 * risk), 2)
        return (
            f"🚨 *STOCK SHORT SCALP SIGNAL ({clean_symbol})* 🔻\n\n"
            f"• Entry Price: *₹{curr_price:,.2f}*\n"
            f"• Target Price: *₹{target:,.2f}* (1:1.8 Risk-Reward)\n"
            f"• Stop Loss: *₹{sl:,.2f}*\n\n"
            f"💡 *വിശകലനം:* ട്രെൻഡ് ബെയറിഷ് ആണ്. പ്രൈസ് VWAP-നും 9 EMA-യ്ക്കും താഴെയാണ്."
        )

    return (
        f"📊 *STOCK ANALYSIS: {clean_symbol}*\n\n"
        f"• Live Price: *₹{curr_price:,.2f}*\n"
        f"• VWAP: *₹{vwap}*\n\n"
        f"⏸️ *വ്യക്തമായ എൻട്രി സിഗ്നൽ ഇല്ല. വിപണി കാണുന്നു...*"
    )


# ==========================================
# 8. INDEX SCANNER ENGINE (NIFTY & SENSEX)
# ==========================================
def scan_index_market(symbol="NIFTY"):
    global ACTIVE_TRADES, LATEST_DATA, JOURNAL_TRADES

    if not is_market_open():
        LATEST_DATA[symbol]["status"] = "മാർക്കറ്റ് ക്ലോസ്ഡ് 🔒"
        return

    df = get_market_data(symbol)
    if df is None:
        return

    curr_spot = round(df["close"].iloc[-1], 2)
    ema_9 = round(df["ema_9"].iloc[-1], 2)
    ema_20 = round(df["ema_20"].iloc[-1], 2)
    vwap = round(df["vwap"].iloc[-1], 2)

    strike_step = 50 if symbol == "NIFTY" else 100
    atm_strike = round(curr_spot / strike_step) * strike_step

    ema_spread = abs(ema_9 - ema_20) / curr_spot * 100
    is_choppy = ema_spread < 0.10

    LATEST_DATA[symbol] = {
        "spot": curr_spot,
        "vwap": vwap,
        "ema9": ema_9,
        "ema20": ema_20,
        "status": "Choppy Market ⏸️" if is_choppy else "Trending 📈",
    }

    active = ACTIVE_TRADES[symbol]

    # Exit Management
    if active:
        target_spot = active["target_spot"]
        sl_spot = active["sl_spot"]
        opt_type = active["type"]

        if (opt_type == "CALL" and curr_spot >= target_spot) or (opt_type == "PUT" and curr_spot <= target_spot):
            msg = (
                f"🎯 *TARGET HIT TRIGGERED ({symbol} {active['strike']} {opt_type})!* 🚀\n\n"
                f"• Spot Exit: *₹{curr_spot:,.2f}*\n"
                f"• Target Spot: *₹{target_spot:,.2f}*\n"
                f"✅ *ലാഭം വിജയകരമായി രേഖപ്പെടുത്തി!*"
            )
            send_telegram_alert(msg)
            JOURNAL_TRADES.append({"symbol": symbol, "type": opt_type, "result": "PROFIT"})
            ACTIVE_TRADES[symbol] = None

        elif (opt_type == "CALL" and curr_spot <= sl_spot) or (opt_type == "PUT" and curr_spot >= sl_spot):
            msg = (
                f"🛑 *STOP LOSS TRIGGERED ({symbol} {active['strike']} {opt_type})!* 🔻\n\n"
                f"• Spot Exit: *₹{curr_spot:,.2f}*\n"
                f"• Stop Loss Level: *₹{sl_spot:,.2f}*\n"
                f"⚠️ *റിസ്ക് മാനേജ്മെന്റ് പ്രകാരം ട്രേഡ് ക്ലോസ് ചെയ്തു.*"
            )
            send_telegram_alert(msg)
            JOURNAL_TRADES.append({"symbol": symbol, "type": opt_type, "result": "LOSS"})
            ACTIVE_TRADES[symbol] = None
        return

    if is_choppy:
        return

    # 1:1.8 Risk Reward Setup
    rr_multiplier = 1.8
    risk_pts = 15.0 if symbol == "NIFTY" else 45.0
    reward_pts = risk_pts * rr_multiplier

    # CALL BUY
    if curr_spot > vwap and ema_9 > ema_20:
        target_spot = round(curr_spot + reward_pts, 2)
        sl_spot = round(curr_spot - risk_pts, 2)

        ACTIVE_TRADES[symbol] = {
            "type": "CALL",
            "strike": atm_strike - strike_step,
            "entry_spot": curr_spot,
            "target_spot": target_spot,
            "sl_spot": sl_spot,
        }

        msg = (
            f"🚨 *NEW BUY {symbol} CALL OPTION!* 🚀\n\n"
            f"• Strike (ITM): *{atm_strike - strike_step} CE*\n"
            f"• Spot Entry: *₹{curr_spot:,.2f}*\n"
            f"• Target Spot: *₹{target_spot:,.2f}* (1:1.8 RR)\n"
            f"• Stop Loss Spot: *₹{sl_spot:,.2f}*\n\n"
            f"💡 *കാരണം:* Spot പ്രൈസ് VWAP-നും 9 EMA-യ്ക്കും മുകളിലാണ്."
        )
        send_telegram_alert(msg)

    # PUT BUY
    elif curr_spot < vwap and ema_9 < ema_20:
        target_spot = round(curr_spot - reward_pts, 2)
        sl_spot = round(curr_spot + risk_pts, 2)

        ACTIVE_TRADES[symbol] = {
            "type": "PUT",
            "strike": atm_strike + strike_step,
            "entry_spot": curr_spot,
            "target_spot": target_spot,
            "sl_spot": sl_spot,
        }

        msg = (
            f"🚨 *NEW BUY {symbol} PUT OPTION!* 🔻\n\n"
            f"• Strike (ITM): *{atm_strike + strike_step} PE*\n"
            f"• Spot Entry: *₹{curr_spot:,.2f}*\n"
            f"• Target Spot: *₹{target_spot:,.2f}* (1:1.8 RR)\n"
            f"• Stop Loss Spot: *₹{sl_spot:,.2f}*\n\n"
            f"💡 *കാരണം:* Spot പ്രൈസ് VWAP-നും 9 EMA-യ്ക്കും താഴെയാണ്."
        )
        send_telegram_alert(msg)


# ==========================================
# 9. EOD ANALYSIS REPORT (10 PM IST)
# ==========================================
def generate_eod_report():
    global EOD_REPORT_SENT

    tz = pytz.timezone("Asia/Kolkata")
    now = datetime.now(tz)

    if now.hour >= 22 and not EOD_REPORT_SENT:
        nifty = LATEST_DATA["NIFTY"]
        sensex = LATEST_DATA["SENSEX"]
        total_trades = len(JOURNAL_TRADES)

        report = (
            f"🌙 *END OF DAY (EOD) MARKET REPORT* 📊\n"
            f"📅 തീയതി: *{now.strftime('%d-%m-%Y')}*\n\n"
            f"📈 *NIFTY 50 Summary:*\n"
            f"• Final Spot: *₹{nifty['spot']:,.2f}*\n"
            f"• Status: *{nifty['status']}*\n\n"
            f"📈 *SENSEX Summary:*\n"
            f"• Final Spot: *₹{sensex['spot']:,.2f}*\n"
            f"• Status: *{sensex['status']}*\n\n"
            f"📝 *ഇന്നത്തെ ട്രേഡിംഗ് സംഗ്രഹം:*\n"
            f"• ആകെ നൽകിയ സിഗ്നലുകൾ: *{total_trades}*\n\n"
            f"ശുഭരാത്രി! നാളത്തെ വിപണിയ്ക്കായി കാത്തിരിക്കുന്നു. 😴"
        )
        send_telegram_alert(report)
        EOD_REPORT_SENT = True

    elif now.hour < 22:
        EOD_REPORT_SENT = False


# ==========================================
# 10. TELEGRAM HANDLERS
# ==========================================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global IS_BOT_ACTIVE
    IS_BOT_ACTIVE = True
    await update.message.reply_text(
        "🚀 *Render 24/7 Scalper & Stock Search Bot Online!*\n\n"
        "ലഭ്യമായ കമാൻഡുകൾ:\n"
        "• *N* - Nifty Current Data\n"
        "• *S* - Sensex Current Data\n"
        "• *NN* - Nifty Active Signals\n"
        "• *SS* - Sensex Active Signals\n"
        "• *NNN* - Nifty Detailed Status\n"
        "• *SSS* - Sensex Detailed Status\n"
        "• *STATUS* - Overall System Status\n"
        "• *Stock Search* - (ഉദാഹരണത്തിന്: `RELIANCE`, `TATAMOTORS`)",
        parse_mode="Markdown",
    )

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global IS_BOT_ACTIVE
    text = update.message.text.strip().upper()

    if text in ["START", "/START"]:
        IS_BOT_ACTIVE = True
        await update.message.reply_text("✅ *സ്കാനിംഗ് ആരംഭിച്ചു...*", parse_mode="Markdown")

    elif text == "N":
        d = LATEST_DATA["NIFTY"]
        msg = (
            f"📊 *NIFTY 50 LIVE DATA:*\n\n"
            f"• Spot Price: *₹{d['spot']:,.2f}*\n"
            f"• VWAP: *₹{d['vwap']}*\n"
            f"• 9 EMA: *₹{d['ema9']}*\n"
            f"• 20 EMA: *₹{d['ema20']}*"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    elif text == "S":
        d = LATEST_DATA["SENSEX"]
        msg = (
            f"📊 *SENSEX LIVE DATA:*\n\n"
            f"• Spot Price: *₹{d['spot']:,.2f}*\n"
            f"• VWAP: *₹{d['vwap']}*\n"
            f"• 9 EMA: *₹{d['ema9']}*\n"
            f"• 20 EMA: *₹{d['ema20']}*"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    elif text in ["NN", "SS"]:
        sym = "NIFTY" if text == "NN" else "SENSEX"
        t = ACTIVE_TRADES[sym]
        if t:
            msg = (
                f"🚨 *{sym} ACTIVE TRADE SIGNAL:*\n\n"
                f"• Option Type: *{t['type']}*\n"
                f"• Strike: *{t['strike']}*\n"
                f"• Entry Spot: *₹{t['entry_spot']:,.2f}*\n"
                f"• Target Spot: *₹{t['target_spot']:,.2f}*\n"
                f"• Stop Loss Spot: *₹{t['sl_spot']:,.2f}*"
            )
        else:
            msg = f"⏸️ *{sym}* - ആക്റ്റീവ് ട്രേഡുകൾ ഒന്നുമില്ല, വിപണി കാണുന്നു..."
        await update.message.reply_text(msg, parse_mode="Markdown")

    elif text in ["NNN", "SSS"]:
        sym = "NIFTY" if text == "NNN" else "SENSEX"
        d = LATEST_DATA[sym]
        t = ACTIVE_TRADES[sym]

        trade_status = (
            f"Type: *{t['type']}* | Strike: *{t['strike']}* | Entry: *₹{t['entry_spot']}*"
            if t
            else "ആക്റ്റീവ് ട്രേഡുകൾ ഒന്നുമില്ല, വിപണി കാണുന്നു ⏸️"
        )

        msg = (
            f"📈 *{sym} FULL DETAILED STATUS:*\n\n"
            f"• Spot Price: *₹{d['spot']:,.2f}*\n"
            f"• Market Condition: *{d['status']}*\n"
            f"• VWAP: *₹{d['vwap']}*\n"
            f"• 9 EMA: *₹{d['ema9']}*\n"
            f"• 20 EMA: *₹{d['ema20']}*\n\n"
            f"🔄 *Trade Status:*\n{trade_status}"
        )
        await update.message.reply_text(msg, parse_mode="Markdown")

    elif text == "STATUS":
        has_active = False
        msg = "🔄 *മാർക്കറ്റ് നിലവിലെ അവസ്ഥ:*\n\n"
        for sym in ["NIFTY", "SENSEX"]:
            t = ACTIVE_TRADES[sym]
            d = LATEST_DATA[sym]
            if t:
                has_active = True
                msg += f"• *{sym}*: Active Trade ({t['type']}) | Entry: ₹{t['entry_spot']}\n"
            else:
                msg += f"• *{sym}*: {d['status']} | Price: ₹{d['spot']}\n"

        if not has_active:
            msg += "\n*ആക്റ്റീവ് ട്രേഡുകൾ ഒന്നുമില്ല, വിശകലനം ചെയ്യുന്നു...*"

        await update.message.reply_text(msg, parse_mode="Markdown")

    else:
        await update.message.reply_text(f"🔍 *{text}* വിപണി വിശകലനം ചെയ്യുന്നു...", parse_mode="Markdown")
        response = analyze_stock_scalp(text)
        await update.message.reply_text(response, parse_mode="Markdown")


# ==========================================
# 11. BACKGROUND SCANNER THREAD
# ==========================================
def background_scanner():
    init_angel_one()
    while True:
        try:
            if IS_BOT_ACTIVE:
                scan_index_market("NIFTY")
                scan_index_market("SENSEX")
                generate_eod_report()
            time.sleep(10)
        except Exception as e:
            print(f"⚠️ Background Loop Exception: {e}")
            time.sleep(10)


# ==========================================
# 12. MAIN EXECUTION
# ==========================================
if __name__ == "__main__":
    print("🚀 Master Option & Stock Scalper Bot (Render Mode) ആരംഭിക്കുന്നു...")

    t_flask = threading.Thread(target=run_flask, daemon=True)
    t_flask.start()

    t_ping = threading.Thread(target=self_ping, daemon=True)
    t_ping.start()

    t_scan = threading.Thread(target=background_scanner, daemon=True)
    t_scan.start()

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), text_handler))

    print("✅ Render Web Server & Telegram ബോട്ട് സംയോജനം പൂർത്തിയായി!")
    app.run_polling()
