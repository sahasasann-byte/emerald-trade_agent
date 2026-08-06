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

# Kotak Neo API SDK Import
try:
    from neo_api_client import NeoAPI
    KOTAK_AVAILABLE = True
except ImportError:
    KOTAK_AVAILABLE = False

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
# 1. CREDENTIALS & ASSET CONFIGURATION
# ==========================================
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8866649004:AAHuRrhqCHqRq0Ucb1i_UyTCG2B5nKOCkps")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "5944911045")

FYERS_CLIENT_ID = os.environ.get("FYERS_CLIENT_ID", "KDE60BKD5D-100")

KOTAK_CLIENT_CODE = os.environ.get("KOTAK_CLIENT_CODE", "XEHM5")
KOTAK_PIN = os.environ.get("KOTAK_PIN", "004482")
KOTAK_CONSUMER_KEY = os.environ.get("KOTAK_CONSUMER_KEY", "9d08c4fe-4395-4057-9752-1e48b42ae317")
KOTAK_CONSUMER_SECRET = os.environ.get("KOTAK_CONSUMER_SECRET", "JJSHPF4MXZQHWHVSXLN6B72XVY")

# For the Self-Pinging Keep-Alive Script
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "")

ALWAYS_ON_ASSETS = {
    "NIFTY": {"fyers": "NSE:NIFTY50-INDEX", "kotak": "NSE_IND:NIFTY 50", "yahoo": "^NSEI", "step": 50, "unit": "pts", "max_hold_mins": 12, "expiry_day": 3},
    "BANK NIFTY": {"fyers": "NSE:NIFTYBANK-INDEX", "kotak": "NSE_IND:NIFTY BANK", "yahoo": "^NSEBANK", "step": 100, "unit": "pts", "max_hold_mins": 12, "expiry_day": 2},
    "SENSEX": {"fyers": "BSE:SENSEX-INDEX", "kotak": "BSE_IND:SENSEX", "yahoo": "^BSESN", "step": 100, "unit": "pts", "max_hold_mins": 12, "expiry_day": 4},
}

ON_DEMAND_ASSETS = {
    "FINNIFTY": {"fyers": "NSE:FINNIFTY-INDEX", "kotak": "NSE_IND:NIFTY FIN SERVICE", "yahoo": "NIFTY_FIN_SERVICE.NS", "step": 50, "unit": "pts", "max_hold_mins": 12, "expiry_day": 1},
    "MIDCPNIFTY": {"fyers": "NSE:MIDCPNIFTY-INDEX", "kotak": "NSE_IND:NIFTY MID SELECT", "yahoo": "^NSEMDCP50", "step": 25, "unit": "pts", "max_hold_mins": 12, "expiry_day": 0},
    "CRUDE OIL": {"fyers": "MCX:CRUDEOIL26AUGFUT", "kotak": "MCX:CRUDEOIL", "yahoo": "CL=F", "step": 10, "unit": "₹/bbl", "is_commodity": True, "max_hold_mins": 25},
    "NATURAL GAS": {"fyers": "MCX:NATURALGAS26AUGFUT", "kotak": "MCX:NATURALGAS", "yahoo": "NG=F", "step": 1, "unit": "₹/mmBtu", "is_commodity": True, "max_hold_mins": 25},
    "GOLD": {"fyers": "MCX:GOLD26OCTFUT", "kotak": "MCX:GOLD", "yahoo": "GC=F", "step": 100, "unit": "₹/10g", "is_commodity": True, "max_hold_mins": 30},
    "SILVER": {"fyers": "MCX:SILVER26SEPFUT", "kotak": "MCX:SILVER", "yahoo": "SI=F", "step": 100, "unit": "₹/kg", "is_commodity": True, "max_hold_mins": 30},
}

STOCK_WATCHLIST = {
    "RELIANCE": {"yahoo": "RELIANCE.NS", "step": 10},
    "HDFCBANK": {"yahoo": "HDFCBANK.NS", "step": 10},
    "ICICIBANK": {"yahoo": "ICICIBANK.NS", "step": 5},
    "INFY": {"yahoo": "INFY.NS", "step": 10},
    "TCS": {"yahoo": "TCS.NS", "step": 20},
    "TATAMOTORS": {"yahoo": "TATAMOTORS.NS", "step": 5},
    "SBIN": {"yahoo": "SBIN.NS", "step": 5},
    "BHARTIARTL": {"yahoo": "BHARTIARTL.NS", "step": 5},
}

ALL_ASSETS = {**ALWAYS_ON_ASSETS, **ON_DEMAND_ASSETS}

fyers = None
kotak_neo = None
STOP_ON_DEMAND_SIGNALS = True 

ACTIVE_SIGNALS = {}
AVOIDED_SIGNALS = {}
DAILY_COMPLETED_TRADES = []

def initialize_broker_apis():
    global fyers, kotak_neo
    
    if FYERS_AVAILABLE:
        token = os.environ.get("FYERS_ACCESS_TOKEN", "")
        if token:
            try:
                fyers = fyersModel.FyersModel(client_id=FYERS_CLIENT_ID, is_async=False, token=token, log_path="")
                logging.info("✅ Fyers API Session Initialized!")
            except Exception as e:
                logging.error(f"❌ Fyers Init Error: {e}")

    if KOTAK_AVAILABLE and KOTAK_CONSUMER_KEY and KOTAK_CONSUMER_SECRET:
        try:
            kotak_neo = NeoAPI(consumer_key=KOTAK_CONSUMER_KEY, consumer_secret=KOTAK_CONSUMER_SECRET, environment="prod")
            if KOTAK_CLIENT_CODE and KOTAK_PIN:
                kotak_neo.login(mobilenumber=KOTAK_CLIENT_CODE, password=KOTAK_PIN)
                logging.info("✅ Kotak Neo API Session Initialized!")
        except Exception as e:
            logging.error(f"❌ Kotak Neo Init Error: {e}")

# ==========================================
# 2. IN-CODE SELF-PINGING SERVER (KEEPS BOT AWAKE)
# ==========================================
def keep_awake_ping():
    if not RENDER_EXTERNAL_URL:
        logging.warning("⚠️ RENDER_EXTERNAL_URL is not set. Bot may go to sleep after 15 mins of inactivity.")
        return
        
    logging.info(f"🔄 Heartbeat Initialized. Pinging {RENDER_EXTERNAL_URL} every 10 minutes.")
    while True:
        try:
            time.sleep(600)  # Wait 10 minutes
            response = requests.get(RENDER_EXTERNAL_URL, timeout=10)
            logging.info(f"💓 Self-Ping Status: {response.status_code} - Bot kept awake!")
        except Exception as e:
            logging.error(f"⚠️ Self-Ping Failed: {e}")

# ==========================================
# 3. OPENING BELL VOLATILITY GUARD
# ==========================================
def is_market_opening_volatility_zone():
    tz = pytz.timezone("Asia/Kolkata")
    now = datetime.now(tz)
    return now.hour == 9 and 15 <= now.minute <= 25

# ==========================================
# 4. TELEGRAM DISPATCHER
# ==========================================
def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logging.error(f"⚠️ Telegram Dispatch Error: {e}")

# ==========================================
# 5. CHART DATA ENGINE (OHLC)
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

def fetch_live_ohlc(asset_name, is_stock=False):
    config = STOCK_WATCHLIST.get(asset_name) if is_stock else ALL_ASSETS.get(asset_name)
    if not config:
        return pd.DataFrame()

    if fyers and not is_stock and "fyers" in config:
        try:
            tz = pytz.timezone("Asia/Kolkata")
            now = datetime.now(tz)
            data = {
                "symbol": config["fyers"],
                "resolution": "3",
                "date_format": "1",
                "range_from": (now - timedelta(days=4)).strftime("%Y-%m-%d"),
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
        yahoo_sym = config["yahoo"]
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}?range=4d&interval=2m"
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
                if asset_name == "GOLD": mult = (usd_inr / 31.1035) * 10
                elif asset_name == "SILVER": mult = (usd_inr / 31.1035) * 1000
                df["open"] *= mult; df["high"] *= mult; df["low"] *= mult; df["close"] *= mult

            df["time"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
            return df
    except Exception as e:
        logging.error(f"❌ Fallback fetch failed for {asset_name}: {e}")

    return pd.DataFrame()

# ==========================================
# 6. EXACT PURE-API PREMIUM ENGINE (NO GUESSWORK)
# ==========================================
def fetch_real_option_premium(asset_name, strike_price, option_type):
    if fyers:
        try:
            exchange = "BSE" if asset_name == "SENSEX" else "NSE"
            fyers_symbol = f"{exchange}:{asset_name}-INDEX"
            res = fyers.optionchain(data={"symbol": fyers_symbol, "strikecount": 5})
            if res and res.get("s") == "ok" and "data" in res:
                for opt in res["data"].get("optionsChain", []):
                    if opt.get("strike_price") == strike_price and opt.get("option_type") == option_type:
                        return max(round(float(opt.get("ltp")), 2), 5.0)
        except Exception:
            pass

    if kotak_neo and asset_name in ALL_ASSETS and "kotak" in ALL_ASSETS[asset_name]:
        try:
            exchange = "bse_fo" if asset_name == "SENSEX" else "nse_fo"
            res = kotak_neo.search_scrip(exchange=exchange, segment="opt", expiry="", name=asset_name, strike=str(strike_price), option_type=option_type)
            if res and len(res) > 0:
                instrument_token = res[0]['pSymbol']
                quote_res = kotak_neo.quote(instrument_token=instrument_token, quote_type="ltp")
                if quote_res and "data" in quote_res:
                    return max(round(float(quote_res["data"]["ltp"]), 2), 5.0)
        except Exception:
            pass

    # 🔥 PURE API ENFORCEMENT: Returns None if exact live premium cannot be fetched.
    return None

# ==========================================
# 7. PERFECTED INDICATORS & DAILY CPR
# ==========================================
def calculate_indicators(df):
    df["ema_5"] = df["close"].ewm(span=5, adjust=False).mean()
    df["ema_9"] = df["close"].ewm(span=9, adjust=False).mean()
    
    df["tp"] = (df["high"] + df["low"] + df["close"]) / 3
    df["vwap"] = (df["tp"] * df["volume"]).cumsum() / df["volume"].cumsum().replace(0, 1)
    
    df["tr"] = np.maximum(df["high"] - df["low"], np.abs(df["high"] - df["close"].shift(1)))
    df["atr"] = df["tr"].rolling(14).mean()
    
    delta = df["close"].diff()
    gain = (delta.where(delta > 0, 0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss.replace(0, 1)
    df["rsi"] = 100 - (100 / (1 + rs))

    df["date"] = df["time"].dt.date
    unique_dates = df["date"].unique()

    if len(unique_dates) > 1:
        prev_date = unique_dates[-2]
        prev_day_df = df[df["date"] == prev_date]
        prev_high = prev_day_df["high"].max()
        prev_low = prev_day_df["low"].min()
        prev_close = prev_day_df.iloc[-1]["close"]
    else:
        prev_high = df["high"].max()
        prev_low = df["low"].min()
        prev_close = df.iloc[-1]["close"]

    pivot = (prev_high + prev_low + prev_close) / 3
    bc = (prev_high + prev_low) / 2
    tc = (pivot - bc) + pivot

    df["cpr_top"] = max(tc, bc)
    df["cpr_bottom"] = min(tc, bc)

    return df

# ==========================================
# 8. QUICK TEXT COMMANDS
# ==========================================
def get_quick_market_summary(asset_name):
    df = fetch_live_ohlc(asset_name)
    if df.empty: return f"⚠️ Unable to fetch live market data for **{asset_name}**."

    df = calculate_indicators(df)
    latest = df.iloc[-1]

    cmp = round(float(latest["close"]), 2)
    today_df = df[df["date"] == latest["date"]]
    open_p = round(float(today_df.iloc[0]["open"]), 2)
    high_p = round(float(today_df["high"].max()), 2)
    low_p = round(float(today_df["low"].min()), 2)
    
    ema_5, ema_9, vwap = float(latest["ema_5"]), float(latest["ema_9"]), float(latest["vwap"])
    cpr_top, cpr_bottom = float(latest["cpr_top"]), float(latest["cpr_bottom"])

    if cmp > vwap and cmp > cpr_top and ema_5 > ema_9: trend = "🟢 BULLISH (Above CPR)"
    elif cmp < vwap and cmp < cpr_bottom and ema_5 < ema_9: trend = "🔴 BEARISH (Below CPR)"
    else: trend = "🟡 SIDEWAYS / CONSOLIDATION (Inside CPR Range)"

    return (
        f"📊 **{asset_name} REAL-TIME OVERVIEW**\n━━━━━━━━━━━━━━━━━━━━━\n"
        f"• **Current Market Price:** **₹{cmp:,.2f}**\n"
        f"• **Market Trend:** **{trend}**\n\n"
        f"📈 **TODAY'S MARKET STATS:**\n"
        f"• **Open:** ₹{open_p:,.2f} | **High:** ₹{high_p:,.2f} | **Low:** ₹{low_p:,.2f}\n"
        f"• **CPR Support/Resistance:** ₹{cpr_bottom:,.2f} - ₹{cpr_top:,.2f}\n"
    )

# ==========================================
# 9. PERFECTED INTRADAY STOCKS SCANNER
# ==========================================
def scan_top_4_stocks():
    selected_stocks = []
    for stock_symbol in STOCK_WATCHLIST:
        df = fetch_live_ohlc(stock_symbol, is_stock=True)
        if df.empty or len(df) < 15: continue
        df = calculate_indicators(df)
        latest = df.iloc[-1]
        
        cmp = round(float(latest["close"]), 2)
        ema_5, ema_9, vwap = latest["ema_5"], latest["ema_9"], latest["vwap"]
        atr = latest["atr"] if not np.isnan(latest["atr"]) else (cmp * 0.01)

        sl_pts = max(round(atr * 1.2, 2), 2.0)
        tp_pts = round(sl_pts * 1.85, 2)

        if cmp > vwap and cmp > latest["cpr_top"] and ema_5 > ema_9:
            selected_stocks.append({"symbol": stock_symbol, "type": "BUY 🟢", "cmp": cmp, "entry": f"₹{cmp - 0.5:,.2f} - ₹{cmp + 0.5:,.2f}", "sl": round(cmp - sl_pts, 2), "tp": round(cmp + tp_pts, 2)})
        elif cmp < vwap and cmp < latest["cpr_bottom"] and ema_5 < ema_9:
            selected_stocks.append({"symbol": stock_symbol, "type": "SELL 🔴", "cmp": cmp, "entry": f"₹{cmp - 0.5:,.2f} - ₹{cmp + 0.5:,.2f}", "sl": round(cmp + sl_pts, 2), "tp": round(cmp - tp_pts, 2)})

        if len(selected_stocks) >= 4: break

    tz = pytz.timezone("Asia/Kolkata")
    time_stamp = datetime.now(tz).strftime('%I:%M:%S %p')

    if not selected_stocks: 
        return f"⚡ **INTRADAY STOCKS SCANNER** ({time_stamp})\n━━━━━━━━━━━━━━━━━━━━━\n💡 *No volume breakout stocks found outside CPR range.*"

    report = f"🎯 **TOP 4 INTRADAY BREAKOUT STOCKS ({time_stamp})**\n━━━━━━━━━━━━━━━━━━━━━\n"
    for idx, s in enumerate(selected_stocks, 1):
        report += (
            f"**{idx}. {s['symbol']} ({s['type']})**\n"
            f"• **CMP:** ₹{s['cmp']:,.2f}\n"
            f"• **Entry Zone:** {s['entry']}\n"
            f"• **Stop Loss (SL):** ₹{s['sl']:,.2f}\n"
            f"• **Target:** ₹{s['tp']:,.2f}\n"
            f"-------------------------------------\n"
        )
    return report

# ==========================================
# 10. PURE-API ANALYSIS ENGINE & AVOIDANCE NOTIFIER
# ==========================================
def analyze_asset_scalp(asset_name, is_auto_scan=False):
    global ACTIVE_SIGNALS, AVOIDED_SIGNALS, STOP_ON_DEMAND_SIGNALS

    tz = pytz.timezone("Asia/Kolkata")
    now_dt = datetime.now(tz)
    time_str = now_dt.strftime("%I:%M:%S %p | %d-%b-%Y")

    if is_auto_scan and is_market_opening_volatility_zone():
        avoid_key = f"{asset_name}_OPENING_BELL"
        if AVOIDED_SIGNALS.get(asset_name) != avoid_key:
            AVOIDED_SIGNALS[asset_name] = avoid_key
            return f"⚠️ **TRADE AVOIDED: {asset_name}** 🛑\n━━━━━━━━━━━━━━━━━━━━━\n• **Reason:** Opening Bell Volatility Zone (09:15 - 09:25 AM)\n• **Safety Filter:** Suppressing entries to prevent wide bid-ask slippage."
        return None

    if is_auto_scan and asset_name in ON_DEMAND_ASSETS and STOP_ON_DEMAND_SIGNALS: return None

    df = fetch_live_ohlc(asset_name)
    if df.empty or len(df) < 15:
        if not is_auto_scan: return f"⚠️ **Data Fetch Error:** Unable to retrieve live price for `{asset_name}`."
        return None

    df = calculate_indicators(df)
    latest = df.iloc[-1]

    curr_price = round(float(latest["close"]), 2)
    ema_5, ema_9, vwap = round(float(latest["ema_5"]), 2), round(float(latest["ema_9"]), 2), round(float(latest["vwap"]), 2)
    rsi = round(float(latest["rsi"]), 1) if not np.isnan(latest["rsi"]) else 50.0
    atr = round(float(latest["atr"]), 2) if not np.isnan(latest["atr"]) else 15.0
    cpr_top, cpr_bottom = round(float(latest["cpr_top"]), 2), round(float(latest["cpr_bottom"]), 2)

    config = ALL_ASSETS[asset_name]
    step, unit = config["step"], config["unit"]
    
    max_hold_mins = config.get("max_hold_mins", 12)
    if "expiry_day" in config and now_dt.weekday() == config["expiry_day"] and now_dt.hour >= 12 and now_dt.minute >= 30:
        max_hold_mins = 6

    atm_strike = round(curr_price / step) * step

    if cpr_bottom <= curr_price <= cpr_top:
        avoid_key = f"{asset_name}_CPR_TRAP"
        if is_auto_scan:
            if (ema_5 > ema_9 or ema_5 < ema_9) and AVOIDED_SIGNALS.get(asset_name) != avoid_key:
                AVOIDED_SIGNALS[asset_name] = avoid_key
                return f"⚠️ **TRADE AVOIDED: {asset_name}** 🛑\n━━━━━━━━━━━━━━━━━━━━━\n• **Reason:** Price Trapped Inside Central Pivot Range\n• **CPR Range:** ₹{cpr_bottom:,.2f} - ₹{cpr_top:,.2f}\n💡 *Filtered to prevent whipsaw losses.*"
            return None
        return f"⚡ **LIVE MARKET ANALYSIS: {asset_name}**\n━━━━━━━━━━━━━━━━━━━━━\n• **Spot CMP:** ₹{curr_price:,.2f} {unit}\n• **Market Context:** **NO TRADE ZONE (Inside CPR Range)**"

    spot_sl_pts = max(round(atr * 1.0, 2), step * 0.3)
    momentum_factor = abs(curr_price - vwap) / atr if atr > 0 else 1.85
    rrr_ratio = min(max(round(momentum_factor, 2), 1.85), 4.0)
    spot_tp_pts = round(spot_sl_pts * rrr_ratio, 2)

    current_signal_type = None

    if curr_price > vwap and curr_price > cpr_top and ema_5 > ema_9:
        if rsi < 52:
            avoid_key = f"{asset_name}_LOW_RSI_BUY"
            if is_auto_scan and AVOIDED_SIGNALS.get(asset_name) != avoid_key:
                AVOIDED_SIGNALS[asset_name] = avoid_key
                return f"⚠️ **TRADE AVOIDED: {asset_name} BUY** 🛑\n━━━━━━━━━━━━━━━━━━━━━\n• **Reason:** Insufficient RSI Momentum ({rsi} < 52)\n💡 *Filtered out fake bullish breakout.*"
            return None
        current_signal_type = "BUY"
        itm_strike = int((atm_strike - step) if asset_name in ["NIFTY", "BANK NIFTY", "SENSEX", "FINNIFTY", "MIDCPNIFTY"] else atm_strike)
        option_pick, opt_type = f"`{itm_strike} CALL (CE)` [ITM High-Delta]", "CE"

    elif curr_price < vwap and curr_price < cpr_bottom and ema_5 < ema_9:
        if rsi > 48:
            avoid_key = f"{asset_name}_HIGH_RSI_SELL"
            if is_auto_scan and AVOIDED_SIGNALS.get(asset_name) != avoid_key:
                AVOIDED_SIGNALS[asset_name] = avoid_key
                return f"⚠️ **TRADE AVOIDED: {asset_name} SELL** 🛑\n━━━━━━━━━━━━━━━━━━━━━\n• **Reason:** Insufficient RSI Momentum ({rsi} > 48)\n💡 *Filtered out fake bearish breakdown.*"
            return None
        current_signal_type = "SELL"
        itm_strike = int((atm_strike + step) if asset_name in ["NIFTY", "BANK NIFTY", "SENSEX", "FINNIFTY", "MIDCPNIFTY"] else atm_strike)
        option_pick, opt_type = f"`{itm_strike} PUT (PE)` [ITM High-Delta]", "PE"
    else:
        if is_auto_scan:
            AVOIDED_SIGNALS[asset_name] = "NEUTRAL"
            return None
        return f"⚡ **LIVE MARKET ANALYSIS: {asset_name}**\n━━━━━━━━━━━━━━━━━━━━━\n• **Spot CMP:** ₹{curr_price:,.2f} {unit}\n• **Market Context:** **NO TRADE ZONE (Awaiting Breakout)**"

    if is_auto_scan:
        active = ACTIVE_SIGNALS.get(asset_name)
        if active and active.get("status") == "OPEN": return None
        if active and active.get("type") == current_signal_type and active.get("status") == "CLOSED": return None

    real_opt_premium = fetch_real_option_premium(asset_name, itm_strike, opt_type)
    if real_opt_premium is None:
        avoid_key = f"{asset_name}_API_PREMIUM_FAIL"
        if is_auto_scan:
            if AVOIDED_SIGNALS.get(asset_name) != avoid_key:
                AVOIDED_SIGNALS[asset_name] = avoid_key
                return f"⚠️ **TRADE AVOIDED: {asset_name}** 🛑\n━━━━━━━━━━━━━━━━━━━━━\n• **Reason:** Broker API offline or unable to fetch Live Option Premium.\n💡 *Filtered out to avoid fake/blind premium calculations.*"
            return None
        return f"⚠️ **Data Fetch Error:** Unable to retrieve precise live Option Premium for `{asset_name}` from Broker API."

    AVOIDED_SIGNALS[asset_name] = "ACTIVE"
    opt_sl_price = round(real_opt_premium - (spot_sl_pts * 0.65), 1)
    opt_tp_price = round(real_opt_premium + (spot_tp_pts * 0.65), 1)

    entry_zone = f"₹{curr_price - (step*0.05):,.2f} - ₹{curr_price + (step*0.05):,.2f}"
    sl = round(curr_price - spot_sl_pts, 2) if current_signal_type == "BUY" else round(curr_price + spot_sl_pts, 2)
    tp = round(curr_price + spot_tp_pts, 2) if current_signal_type == "BUY" else round(curr_price - spot_tp_pts, 2)
    
    expiry_time = now_dt + timedelta(minutes=max_hold_mins)
    
    ACTIVE_SIGNALS[asset_name] = {
        "asset": asset_name, "type": current_signal_type, "option": option_pick,
        "entry_cmp": curr_price, "sl": sl, "tp": tp, "rrr": rrr_ratio,
        "opt_premium": real_opt_premium, "opt_sl": opt_sl_price, "opt_tp": opt_tp_price,
        "start_dt": now_dt, "expiry_dt": expiry_time, "time_str": time_str, "status": "OPEN", "trailed_to_cost": False
    }

    signal_header = f"🛑 🚨 **HIGH-CONVICTION ALERT: {current_signal_type} [RRR 1:{rrr_ratio}]** 🔴 🛑" if rrr_ratio >= 2.0 else f"🚨 **NEW SCALP SIGNAL: {current_signal_type}** 🟢"
    bias_desc = f"Order Flow breakout above CPR with RSI ({rsi})." if current_signal_type == "BUY" else f"Order Flow breakdown below CPR with RSI ({rsi})."

    return (
        f"{signal_header}\n━━━━━━━━━━━━━━━━━━━━━\n⏰ **Signal Time:** `{time_str}`\n• **Asset:** `{asset_name}`\n"
        f"• **Current Spot Price (CMP):** **₹{curr_price:,.2f} {unit}**\n• **Option Strike Pick:** {option_pick}\n"
        f"• **REAL ITM Option Premium:** **₹{real_opt_premium:,.1f}**\n• **Setup:** {bias_desc}\n\n"
        f"🎯 **TARGETS & RISK LEVELS**\n• **Spot Entry Zone:** {entry_zone}\n• **Spot SL:** **₹{sl:,.2f}**\n"
        f"• **Spot Target (TP):** **₹{tp:,.2f}**\n• **Option Premium SL:** **₹{opt_sl_price:,.1f}**\n"
        f"• **Option Premium Target:** **₹{opt_tp_price:,.1f}**\n• **Risk-Reward Ratio:** **1 : {rrr_ratio}**\n\n"
        f"⏳ **THETA DECAY GUARD:** Max {max_hold_mins} Mins | Hard Exit: `{expiry_time.strftime('%I:%M %p')}`"
    )

# ==========================================
# 11. SEGMENT-SPECIFIC EOD P&L CALCULATOR
# ==========================================
def calculate_eod_performance(capital=10000.0, asset_filter=None):
    global DAILY_COMPLETED_TRADES
    tz = pytz.timezone("Asia/Kolkata")
    today_str = datetime.now(tz).strftime("%d-%b-%Y")

    filtered_trades = DAILY_COMPLETED_TRADES
    segment_label = "ALL SEGMENTS COMBINED"

    if asset_filter:
        if asset_filter == "STOCKS":
            filtered_trades = [t for t in DAILY_COMPLETED_TRADES if t["asset"] in STOCK_WATCHLIST]
            segment_label = "INTRADAY STOCKS"
        else:
            filtered_trades = [t for t in DAILY_COMPLETED_TRADES if t["asset"] == asset_filter]
            segment_label = f"{asset_filter} SEGMENT"

    if not filtered_trades:
        return f"📊 **EOD PERFORMANCE REPORT: {segment_label} ({today_str})**\n━━━━━━━━━━━━━━━━━━━━━\n• **Status:** No trades completed in this segment today."

    total_trades = len(filtered_trades)
    wins = [t for t in filtered_trades if t["result"] == "WIN"]
    losses = [t for t in filtered_trades if t["result"] == "LOSS"]
    breakevens = [t for t in filtered_trades if t["result"] == "BREAKEVEN"]
    
    win_count = len(wins)
    loss_count = len(losses)
    be_count = len(breakevens)
    
    win_rate = (win_count / total_trades) * 100 if total_trades > 0 else 0.0

    risk_per_trade = capital * 0.02
    gross_profit = (win_count * (risk_per_trade * 1.85)) - (loss_count * risk_per_trade)
    total_brokerage = total_trades * 20.0
    net_earnings = gross_profit - total_brokerage

    return (
        f"📊 **EOD PERFORMANCE REPORT: {segment_label} ({today_str})**\n━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 **Allocated Account Capital:** **₹{capital:,.2f}**\n\n📈 **SIGNAL PERFORMANCE:**\n"
        f"• **Total Trades Executed:** {total_trades}\n• **Winning Signals:** {win_count} 🟢\n"
        f"• **Breakeven Signals (Trailed SL Hit):** {be_count} 🟡\n"
        f"• **Losing Signals:** {loss_count} 🔴\n• **Strategy Win Rate:** **{win_rate:.1f}%**\n\n"
        f"💵 **NET EVENING EARNINGS (AFTER TAX & BROKERAGE):**\n• **Gross Profit:** ₹{gross_profit:,.2f}\n"
        f"• **Taxes & Exchange Fees (~₹20/trade):** -₹{total_brokerage:,.2f}\n"
        f"• 🏆 **NET TAKE-HOME EARNINGS:** **₹{net_earnings:,.2f}** ({(net_earnings / capital) * 100:+.2f}% ROI)\n"
    )

# ==========================================
# 12. LIVE TRADE TRACKER & TRAILING SL TO COST
# ==========================================
def track_active_trades():
    global ACTIVE_SIGNALS, DAILY_COMPLETED_TRADES
    tz = pytz.timezone("Asia/Kolkata")
    now_dt = datetime.now(tz)

    for asset_name, trade in list(ACTIVE_SIGNALS.items()):
        if not trade or trade.get("status") != "OPEN": continue

        df = fetch_live_ohlc(asset_name)
        if df.empty: continue

        latest = df.iloc[-1]
        high_price, low_price, cmp = float(latest["high"]), float(latest["low"]), float(latest["close"])
        sig_type, sl, tp, entry, option = trade["type"], trade["sl"], trade["tp"], trade["entry_cmp"], trade["option"]
        sl_distance = abs(entry - sl)

        if not trade.get("trailed_to_cost", False):
            if (sig_type == "BUY" and (high_price - entry) >= sl_distance) or (sig_type == "SELL" and (entry - low_price) >= sl_distance):
                ACTIVE_SIGNALS[asset_name]["sl"] = entry
                ACTIVE_SIGNALS[asset_name]["trailed_to_cost"] = True
                send_telegram_alert(f"🛡️ **ZERO-RISK TRAILING SL:** `{asset_name}` hit 1:1 RRR! Stop Loss moved to Entry Cost (₹{entry:,.2f}).")

        if now_dt >= trade["expiry_dt"]:
            ACTIVE_SIGNALS[asset_name]["status"] = "CLOSED"
            pnl_pts = round(cmp - entry if sig_type == "BUY" else entry - cmp, 2)
            res = "WIN" if pnl_pts > 0 else "LOSS"
            DAILY_COMPLETED_TRADES.append({"asset": asset_name, "result": res})
            send_telegram_alert(f"⏳ **THETA EROSION AUTO-EXIT:** `{asset_name}` ({option})\nClosed at CMP ₹{cmp:,.2f} to save option premium.")
            continue

        if (sig_type == "BUY" and (high_price >= tp or cmp >= tp)) or (sig_type == "SELL" and (low_price <= tp or cmp <= tp)):
            ACTIVE_SIGNALS[asset_name]["status"] = "CLOSED"
            DAILY_COMPLETED_TRADES.append({"asset": asset_name, "result": "WIN"})
            send_telegram_alert(f"🎯 **TARGET ACHIEVED!** 🎉🟢\n• **Asset:** `{asset_name}` ({option})\n• **Result:** **WIN SECURED!** 🚀")
            
        elif (sig_type == "BUY" and (low_price <= sl or cmp <= sl)) or (sig_type == "SELL" and (high_price >= sl or cmp >= sl)):
            ACTIVE_SIGNALS[asset_name]["status"] = "CLOSED"
            if trade.get("trailed_to_cost", False) and sl == entry:
                DAILY_COMPLETED_TRADES.append({"asset": asset_name, "result": "BREAKEVEN"})
                send_telegram_alert(f"🛡️ **TRAILED SL HIT (BREAKEVEN)** 🟡\n• **Asset:** `{asset_name}` ({option})\n• **Exited at Cost:** ₹{sl:,.2f}")
            else:
                DAILY_COMPLETED_TRADES.append({"asset": asset_name, "result": "LOSS"})
                send_telegram_alert(f"🛑 **STOP LOSS HIT** 🔴\n• **Asset:** `{asset_name}` ({option})\n• **SL Hit:** ₹{sl:,.2f}")

# ==========================================
# 13. BACKGROUND SCANNER THREAD
# ==========================================
def background_all_segment_scanner():
    logging.info("🚀 Background Scanner Active 24/7!")
    time.sleep(10)
    eod_reported_today = False

    while True:
        try:
            tz = pytz.timezone("Asia/Kolkata")
            now_dt = datetime.now(tz)

            if now_dt.hour == 15 and now_dt.minute == 30 and not eod_reported_today:
                send_telegram_alert(calculate_eod_performance(10000.0))
                eod_reported_today = True
            elif now_dt.hour != 15:
                eod_reported_today = False

            for asset in ALWAYS_ON_ASSETS:
                alert = analyze_asset_scalp(asset, is_auto_scan=True)
                if alert: send_telegram_alert(alert)
                time.sleep(2)

            if not STOP_ON_DEMAND_SIGNALS:
                for asset in ON_DEMAND_ASSETS:
                    alert = analyze_asset_scalp(asset, is_auto_scan=True)
                    if alert: send_telegram_alert(alert)
                    time.sleep(2)

            track_active_trades()

        except Exception as e:
            logging.error(f"⚠️ Scanner Error: {e}")
        time.sleep(30)

# ==========================================
# 14. RENDER WEB SERVER
# ==========================================
app_flask = Flask(__name__)
@app_flask.route("/")
def home(): return "🚀 Emerald Trade Agent Live!"
def run_flask(): app_flask.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)), debug=False, use_reloader=False)

# ==========================================
# 15. TELEGRAM HANDLERS
# ==========================================
def get_main_keyboard():
    pause_text = "▶️ RESUME SIGNALS" if STOP_ON_DEMAND_SIGNALS else "🛑 PAUSE SIGNALS"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 NIFTY", callback_data="ANALYZE_NIFTY"), InlineKeyboardButton("🏦 BANK NIFTY", callback_data="ANALYZE_BANK NIFTY"), InlineKeyboardButton("📊 SENSEX", callback_data="ANALYZE_SENSEX")],
        [InlineKeyboardButton("🎯 STOCKS", callback_data="TRIGGER_STOCKS"), InlineKeyboardButton("🔷 FINNIFTY", callback_data="ANALYZE_FINNIFTY"), InlineKeyboardButton("⚡ MIDCPNIFTY", callback_data="ANALYZE_MIDCPNIFTY")],
        [InlineKeyboardButton("🛢️ CRUDE OIL", callback_data="ANALYZE_CRUDE OIL"), InlineKeyboardButton("🔥 NATURAL GAS", callback_data="ANALYZE_NATURAL GAS")],
        [InlineKeyboardButton("🥇 GOLD", callback_data="ANALYZE_GOLD"), InlineKeyboardButton("🥈 SILVER", callback_data="ANALYZE_SILVER")],
        [InlineKeyboardButton(pause_text, callback_data="TOGGLE_SIGNALS"), InlineKeyboardButton("📊 EOD P&L REPORT", callback_data="EOD_REPORT")]
    ])

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = ("🚀 *Institutional Trading Control Panel!*\n\n⚡ **QUICK COMMANDS:**\n• `N` $\\rightarrow$ NIFTY Stats\n• `S` $\\rightarrow$ SENSEX Stats\n• `B` $\\rightarrow$ BANK NIFTY Stats\n\n📊 **EOD COMMANDS:**\n• `EODN` $\\rightarrow$ NIFTY Report\n• `EODS` $\\rightarrow$ SENSEX Report\n• `EODBN` $\\rightarrow$ BANK NIFTY Report\n• `EODST` $\\rightarrow$ STOCKS Report\n• `EOD` $\\rightarrow$ Combined Report")
    await update.message.reply_text(msg, reply_markup=get_main_keyboard(), parse_mode="Markdown")

async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper()
    if text == "N": await update.message.reply_text(get_quick_market_summary("NIFTY"), parse_mode="Markdown", reply_markup=get_main_keyboard())
    elif text == "S": await update.message.reply_text(get_quick_market_summary("SENSEX"), parse_mode="Markdown", reply_markup=get_main_keyboard())
    elif text == "B": await update.message.reply_text(get_quick_market_summary("BANK NIFTY"), parse_mode="Markdown", reply_markup=get_main_keyboard())
    elif text in ["EODN", "EODL"]: await update.message.reply_text(calculate_eod_performance(10000.0, "NIFTY"), parse_mode="Markdown", reply_markup=get_main_keyboard())
    elif text == "EODS": await update.message.reply_text(calculate_eod_performance(10000.0, "SENSEX"), parse_mode="Markdown", reply_markup=get_main_keyboard())
    elif text == "EODBN": await update.message.reply_text(calculate_eod_performance(10000.0, "BANK NIFTY"), parse_mode="Markdown", reply_markup=get_main_keyboard())
    elif text in ["EODST", "EODSTOCKS"]: await update.message.reply_text(calculate_eod_performance(10000.0, "STOCKS"), parse_mode="Markdown", reply_markup=get_main_keyboard())
    elif text == "EOD": await update.message.reply_text(calculate_eod_performance(10000.0), parse_mode="Markdown", reply_markup=get_main_keyboard())

async def eod_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(calculate_eod_performance(10000.0), reply_markup=get_main_keyboard(), parse_mode="Markdown")

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global STOP_ON_DEMAND_SIGNALS
    query = update.callback_query
    await query.answer()
    
    if query.data == "TRIGGER_STOCKS":
        await query.edit_message_text("🔍 Scanning...", parse_mode="Markdown")
        await context.bot.send_message(chat_id=query.message.chat_id, text=scan_top_4_stocks(), parse_mode="Markdown", reply_markup=get_main_keyboard())
    elif query.data == "TOGGLE_SIGNALS":
        STOP_ON_DEMAND_SIGNALS = not STOP_ON_DEMAND_SIGNALS
        status_msg = "🛑 **ON-DEMAND SIGNALS PAUSED!**" if STOP_ON_DEMAND_SIGNALS else "▶️ **SIGNALS RESUMED!**"
        await context.bot.send_message(chat_id=query.message.chat_id, text=status_msg, parse_mode="Markdown", reply_markup=get_main_keyboard())
    elif query.data == "EOD_REPORT":
        await context.bot.send_message(chat_id=query.message.chat_id, text=calculate_eod_performance(10000.0), parse_mode="Markdown", reply_markup=get_main_keyboard())
    elif query.data.startswith("ANALYZE_"):
        asset = query.data.replace("ANALYZE_", "")
        report = analyze_asset_scalp(asset, is_auto_scan=False)
        if report: await context.bot.send_message(chat_id=query.message.chat_id, text=report, parse_mode="Markdown", reply_markup=get_main_keyboard())

if __name__ == "__main__":
    initialize_broker_apis()
    
    threading.Thread(target=keep_awake_ping, daemon=True).start()
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=background_all_segment_scanner, daemon=True).start()
    
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("eod", eod_command))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), text_message_handler))
    app.add_handler(CallbackQueryHandler(button_callback_handler))
    
    logging.info("✅ Starting Telegram Bot...")
    app.run_polling(drop_pending_updates=True)
