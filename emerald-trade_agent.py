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

# FYERS API Credentials
FYERS_CLIENT_ID = os.environ.get("FYERS_CLIENT_ID", "KDE60BKD5D-100")

# KOTAK NEO API Credentials
KOTAK_CLIENT_CODE = os.environ.get("KOTAK_CLIENT_CODE", "XEHM5")
KOTAK_PIN = os.environ.get("KOTAK_PIN", "004482")
KOTAK_CONSUMER_KEY = os.environ.get("KOTAK_CONSUMER_KEY", "9d08c4fe-4395-4057-9752-1e48b42ae317")
KOTAK_CONSUMER_SECRET = os.environ.get("KOTAK_CONSUMER_SECRET", "JJSHPF4MXZQHWHVSXLN6B72XVY")

# ALWAYS-ON ASSETS (24/7 Background Scanning)
ALWAYS_ON_ASSETS = {
    "NIFTY": {"fyers": "NSE:NIFTY50-INDEX", "kotak": "NSE_IND:NIFTY 50", "yahoo": "^NSEI", "step": 50, "unit": "pts", "max_hold_mins": 20, "base_premium_pct": 0.0055},
    "BANK NIFTY": {"fyers": "NSE:NIFTYBANK-INDEX", "kotak": "NSE_IND:NIFTY BANK", "yahoo": "^NSEBANK", "step": 100, "unit": "pts", "max_hold_mins": 15, "base_premium_pct": 0.0065},
    "SENSEX": {"fyers": "BSE:SENSEX-INDEX", "kotak": "BSE_IND:SENSEX", "yahoo": "^BSESN", "step": 100, "unit": "pts", "max_hold_mins": 15, "base_premium_pct": 0.0050},
}

# ON-DEMAND ASSETS (Triggered via Buttons)
ON_DEMAND_ASSETS = {
    "FINNIFTY": {"fyers": "NSE:FINNIFTY-INDEX", "kotak": "NSE_IND:NIFTY FIN SERVICE", "yahoo": "NIFTY_FIN_SERVICE.NS", "step": 50, "unit": "pts", "max_hold_mins": 15, "base_premium_pct": 0.0055},
    "MIDCPNIFTY": {"fyers": "NSE:MIDCPNIFTY-INDEX", "kotak": "NSE_IND:NIFTY MID SELECT", "yahoo": "^NSEMDCP50", "step": 25, "unit": "pts", "max_hold_mins": 15, "base_premium_pct": 0.0055},
    "CRUDE OIL": {"fyers": "MCX:CRUDEOIL26AUGFUT", "kotak": "MCX:CRUDEOIL", "yahoo": "CL=F", "step": 10, "unit": "₹/bbl", "is_commodity": True, "max_hold_mins": 30, "base_premium_pct": 0.015},
    "NATURAL GAS": {"fyers": "MCX:NATURALGAS26AUGFUT", "kotak": "MCX:NATURALGAS", "yahoo": "NG=F", "step": 1, "unit": "₹/mmBtu", "is_commodity": True, "max_hold_mins": 30, "base_premium_pct": 0.020},
    "GOLD": {"fyers": "MCX:GOLD26OCTFUT", "kotak": "MCX:GOLD", "yahoo": "GC=F", "step": 100, "unit": "₹/10g", "is_commodity": True, "max_hold_mins": 45, "base_premium_pct": 0.008},
    "SILVER": {"fyers": "MCX:SILVER26SEPFUT", "kotak": "MCX:SILVER", "yahoo": "SI=F", "step": 100, "unit": "₹/kg", "is_commodity": True, "max_hold_mins": 45, "base_premium_pct": 0.010},
}

# WATCHLIST FOR INTRADAY STOCKS SCANNER
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
STOP_ON_DEMAND_SIGNALS = False

ACTIVE_SIGNALS = {}
DAILY_COMPLETED_TRADES = []

def initialize_broker_apis():
    global fyers, kotak_neo
    
    # 1. Initialize Fyers API
    if FYERS_AVAILABLE:
        token = os.environ.get("FYERS_ACCESS_TOKEN", "")
        if token:
            try:
                fyers = fyersModel.FyersModel(client_id=FYERS_CLIENT_ID, is_async=False, token=token, log_path="")
                logging.info("✅ Fyers API Session Initialized!")
            except Exception as e:
                logging.error(f"❌ Fyers Init Error: {e}")

    # 2. Initialize Kotak Neo API Fallback Engine
    if KOTAK_AVAILABLE and KOTAK_CONSUMER_KEY and KOTAK_CONSUMER_SECRET:
        try:
            kotak_neo = NeoAPI(
                consumer_key=KOTAK_CONSUMER_KEY,
                consumer_secret=KOTAK_CONSUMER_SECRET,
                environment="prod"
            )
            if KOTAK_CLIENT_CODE and KOTAK_PIN:
                kotak_neo.login(mobilenumber=KOTAK_CLIENT_CODE, password=KOTAK_PIN)
                logging.info("✅ Kotak Neo API Session Initialized Successfully!")
        except Exception as e:
            logging.error(f"❌ Kotak Neo Init Error: {e}")

# ==========================================
# 2. TELEGRAM ALERT DISPATCHER
# ==========================================
def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        logging.error(f"⚠️ Telegram Dispatch Error: {e}")

# ==========================================
# 3. TRIPLE DATA ENGINE (FYERS -> KOTAK NEO -> WEB)
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

    # Priority 1: Direct Fyers API
    if fyers and not is_stock and "fyers" in config:
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

    # Priority 2: Kotak Neo API Fallback
    if kotak_neo and not is_stock and "kotak" in config:
        try:
            res = kotak_neo.quote(instrument_token=config["kotak"], quote_type="ltp")
            if res and "data" in res:
                latest_price = float(res["data"]["ltp"])
                timestamps = [int(time.time()) - (i * 180) for i in range(20, 0, -1)]
                df = pd.DataFrame({
                    "timestamp": timestamps,
                    "open": [latest_price] * 20,
                    "high": [latest_price + 2.0] * 20,
                    "low": [latest_price - 2.0] * 20,
                    "close": [latest_price] * 20,
                    "volume": [1000] * 20
                })
                df["time"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata")
                return df
        except Exception as e:
            logging.warning(f"⚠️ Kotak Neo quote fetch failed for {asset_name}: {e}")

    # Priority 3: Yahoo Finance Web Engine Fallback
    try:
        yahoo_sym = config["yahoo"]
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_sym}?range=2d&interval=2m"
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

def calculate_indicators(df):
    df["ema_5"] = df["close"].ewm(span=5, adjust=False).mean()
    df["ema_9"] = df["close"].ewm(span=9, adjust=False).mean()
    df["tp"] = (df["high"] + df["low"] + df["close"]) / 3
    df["vwap"] = (df["tp"] * df["volume"]).cumsum() / df["volume"].cumsum().replace(0, 1)
    df["tr"] = np.maximum(df["high"] - df["low"], np.abs(df["high"] - df["close"].shift(1)))
    df["atr"] = df["tr"].rolling(14).mean()
    return df

# ==========================================
# 4. QUICK TEXT COMMANDS (N, S, B)
# ==========================================
def get_quick_market_summary(asset_name):
    df = fetch_live_ohlc(asset_name)
    if df.empty:
        return f"⚠️ Unable to fetch live market data for **{asset_name}**."

    df = calculate_indicators(df)
    latest = df.iloc[-1]

    cmp = round(float(latest["close"]), 2)
    open_p = round(float(df.iloc[0]["open"]), 2)
    high_p = round(float(df["high"].max()), 2)
    low_p = round(float(df["low"].min()), 2)
    prev_close = round(float(df.iloc[0]["close"]), 2)

    ema_5 = float(latest["ema_5"])
    ema_9 = float(latest["ema_9"])
    vwap = float(latest["vwap"])

    if cmp > vwap and ema_5 > ema_9:
        trend = "🟢 BULLISH (Upward Momentum)"
    elif cmp < vwap and ema_5 < ema_9:
        trend = "🔴 BEARISH (Downward Momentum)"
    else:
        trend = "🟡 SIDEWAYS / CONSOLIDATION"

    return (
        f"📊 **{asset_name} REAL-TIME OVERVIEW**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"• **Current Market Price (CMP):** **₹{cmp:,.2f}**\n"
        f"• **Market Trend:** **{trend}**\n\n"
        f"📈 **TODAY'S MARKET STATS:**\n"
        f"• **Open:** ₹{open_p:,.2f}\n"
        f"• **Today's High:** ₹{high_p:,.2f}\n"
        f"• **Today's Low:** ₹{low_p:,.2f}\n"
        f"• **Previous Close:** ₹{prev_close:,.2f}\n"
    )

# ==========================================
# 5. INTRADAY STOCKS SCANNER
# ==========================================
def scan_top_4_stocks():
    selected_stocks = []
    
    for stock_symbol in STOCK_WATCHLIST:
        df = fetch_live_ohlc(stock_symbol, is_stock=True)
        if df.empty or len(df) < 15:
            continue

        df = calculate_indicators(df)
        latest = df.iloc[-1]
        
        cmp = round(float(latest["close"]), 2)
        ema_5 = round(float(latest["ema_5"]), 2)
        ema_9 = round(float(latest["ema_9"]), 2)
        vwap = round(float(latest["vwap"]), 2)
        atr = round(float(latest["atr"]), 2) if not np.isnan(latest["atr"]) else (cmp * 0.01)

        sl_pts = max(round(atr * 1.2, 2), 2.0)
        tp_pts = round(sl_pts * 1.85, 2)

        if cmp > vwap and ema_5 > ema_9:
            selected_stocks.append({
                "symbol": stock_symbol, "type": "BUY 🟢", "cmp": cmp,
                "entry": f"₹{cmp - 0.5:,.2f} - ₹{cmp + 0.5:,.2f}",
                "sl": round(cmp - sl_pts, 2), "tp": round(cmp + tp_pts, 2),
            })
        elif cmp < vwap and ema_5 < ema_9:
            selected_stocks.append({
                "symbol": stock_symbol, "type": "SELL 🔴", "cmp": cmp,
                "entry": f"₹{cmp - 0.5:,.2f} - ₹{cmp + 0.5:,.2f}",
                "sl": round(cmp + sl_pts, 2), "tp": round(cmp - tp_pts, 2),
            })

        if len(selected_stocks) >= 4:
            break

    if not selected_stocks:
        return "⚡ **INTRADAY STOCKS SCANNER**\n━━━━━━━━━━━━━━━━━━━━━\n💡 *No high-conviction volume breakout stocks found right now.*"

    tz = pytz.timezone("Asia/Kolkata")
    now_str = datetime.now(tz).strftime("%I:%M:%S %p")

    report = f"🎯 **TOP 4 INTRADAY BREAKOUT STOCKS ({now_str})**\n━━━━━━━━━━━━━━━━━━━━━\n"
    for idx, s in enumerate(selected_stocks, 1):
        report += (
            f"**{idx}. {s['symbol']}** ({s['type']})\n"
            f"• **CMP:** ₹{s['cmp']:,.2f}\n"
            f"• **Entry Zone:** {s['entry']}\n"
            f"• **Stop Loss (SL):** ₹{s['sl']:,.2f}\n"
            f"• **Target:** ₹{s['tp']:,.2f}\n"
            f"-------------------------------------\n"
        )
    return report

# ==========================================
# 6. ANALYSIS ENGINE WITH HIGH RRR ALERT
# ==========================================
def analyze_asset_scalp(asset_name, is_auto_scan=False):
    global ACTIVE_SIGNALS, STOP_ON_DEMAND_SIGNALS

    if is_auto_scan and asset_name in ON_DEMAND_ASSETS and STOP_ON_DEMAND_SIGNALS:
        return None

    df = fetch_live_ohlc(asset_name)
    if df.empty or len(df) < 15:
        if not is_auto_scan:
            return f"⚠️ **Data Fetch Error:** Unable to retrieve live price for `{asset_name}`."
        return None

    df = calculate_indicators(df)
    latest = df.iloc[-1]
    
    tz = pytz.timezone("Asia/Kolkata")
    now_dt = datetime.now(tz)
    time_str = now_dt.strftime("%I:%M:%S %p | %d-%b-%Y")

    curr_price = round(float(latest["close"]), 2)
    ema_5 = round(float(latest["ema_5"]), 2)
    ema_9 = round(float(latest["ema_9"]), 2)
    vwap = round(float(latest["vwap"]), 2)
    atr = round(float(latest["atr"]), 2) if not np.isnan(latest["atr"]) else 15.0

    config = ALL_ASSETS[asset_name]
    step = config["step"]
    unit = config["unit"]
    max_hold_mins = config["max_hold_mins"]
    
    atm_strike = round(curr_price / step) * step
    estimated_option_premium = round(curr_price * config["base_premium_pct"], 1)

    spot_sl_pts = max(round(atr * 1.0, 2), step * 0.3)
    momentum_factor = abs(curr_price - vwap) / atr if atr > 0 else 1.85
    rrr_ratio = min(max(round(momentum_factor, 2), 1.85), 4.0)

    spot_tp_pts = round(spot_sl_pts * rrr_ratio, 2)
    option_sl_pts = round(spot_sl_pts * 0.50, 1)
    option_tp_pts = round(spot_tp_pts * 0.50, 1)

    option_sl_price = max(round(estimated_option_premium - option_sl_pts, 1), 1.0)
    option_tp_price = round(estimated_option_premium + option_tp_pts, 1)

    current_signal_type = None

    if curr_price > vwap and ema_5 > ema_9:
        current_signal_type = "BUY"
        strike_val = int(atm_strike - step if asset_name in ["NIFTY", "BANK NIFTY", "SENSEX", "FINNIFTY", "MIDCPNIFTY"] else atm_strike)
        option_pick = f"`{strike_val} CALL (CE)`"
        entry_zone = f"₹{curr_price - (step*0.05):,.2f} - ₹{curr_price + (step*0.05):,.2f}"
        sl = round(curr_price - spot_sl_pts, 2)
        tp = round(curr_price + spot_tp_pts, 2)
        
        if rrr_ratio >= 2.0:
            signal_header = f"🛑 🚨 **HIGH-CONVICTION ALERT: BUY CALL (CE) [RRR 1:{rrr_ratio}]** 🔴 🛑"
        else:
            signal_header = "🚨 **NEW SCALP SIGNAL: BUY CALL (CE)** 🟢"

        bias_desc = f"Order Flow expansion above VWAP with 5/9 EMA momentum."

    elif curr_price < vwap and ema_5 < ema_9:
        current_signal_type = "SELL"
        strike_val = int(atm_strike + step if asset_name in ["NIFTY", "BANK NIFTY", "SENSEX", "FINNIFTY", "MIDCPNIFTY"] else atm_strike)
        option_pick = f"`{strike_val} PUT (PE)`"
        entry_zone = f"₹{curr_price - (step*0.05):,.2f} - ₹{curr_price + (step*0.05):,.2f}"
        sl = round(curr_price + spot_sl_pts, 2)
        tp = round(curr_price - spot_tp_pts, 2)
        
        if rrr_ratio >= 2.0:
            signal_header = f"🛑 🚨 **HIGH-CONVICTION ALERT: BUY PUT (PE) [RRR 1:{rrr_ratio}]** 🔴 🛑"
        else:
            signal_header = "🚨 **NEW SCALP SIGNAL: BUY PUT (PE)** 🔴"

        bias_desc = f"Order Flow breakdown below VWAP with 5/9 EMA expansion."
    else:
        if is_auto_scan:
            return None
        return (
            f"⚡ **LIVE MARKET ANALYSIS: {asset_name}**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"• **Spot CMP:** ₹{curr_price:,.2f} {unit}\n"
            f"• **Live VWAP:** ₹{vwap:,.2f} | **5 EMA:** ₹{ema_5:,.2f}\n"
            f"• **Market Context:** **NO TRADE ZONE (Consolidation)**\n\n"
            f"💡 *Price trapped near VWAP. Preserving capital until clean breakout.*"
        )

    if is_auto_scan:
        active = ACTIVE_SIGNALS.get(asset_name)
        if active and active.get("status") == "OPEN":
            return None
        if active and active.get("type") == current_signal_type and active.get("status") == "CLOSED":
            return None

    expiry_time = now_dt + timedelta(minutes=max_hold_mins)
    
    ACTIVE_SIGNALS[asset_name] = {
        "asset": asset_name, "type": current_signal_type, "option": option_pick,
        "entry_cmp": curr_price, "sl": sl, "tp": tp, "rrr": rrr_ratio,
        "opt_premium": estimated_option_premium, "opt_sl": option_sl_price, "opt_tp": option_tp_price,
        "start_dt": now_dt, "expiry_dt": expiry_time, "time_str": time_str, "status": "OPEN"
    }

    report = (
        f"{signal_header}\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ **Signal Time:** `{time_str}`\n"
        f"• **Asset:** `{asset_name}`\n"
        f"• **Current Spot Price (CMP):** **₹{curr_price:,.2f} {unit}**\n"
        f"• **Option Strike Pick:** {option_pick}\n"
        f"• **Est. Premium Price:** **~₹{estimated_option_premium:,.1f}**\n"
        f"• **Setup:** {bias_desc}\n\n"
        f"🎯 **TARGETS & RISK LEVELS**\n"
        f"• **Spot Entry Zone:** {entry_zone}\n"
        f"• **Spot SL:** **₹{sl:,.2f}** (Risk: {spot_sl_pts:.2f} pts)\n"
        f"• **Spot Target (TP):** **₹{tp:,.2f}** (Reward: {spot_tp_pts:.2f} pts)\n"
        f"• **Option SL:** ~₹{option_sl_price:,.1f}\n"
        f"• **Option Target:** ~₹{option_tp_price:,.1f}\n"
        f"• **Risk-Reward Ratio:** **1 : {rrr_ratio}**\n\n"
        f"⏳ **HOLDING RULES:** Max {max_hold_mins} Mins | Hard Exit: `{expiry_time.strftime('%I:%M %p')}`"
    )

    return report

# ==========================================
# 7. END OF DAY P&L CALCULATOR
# ==========================================
def calculate_eod_performance(capital=10000.0):
    global DAILY_COMPLETED_TRADES
    
    tz = pytz.timezone("Asia/Kolkata")
    today_str = datetime.now(tz).strftime("%d-%b-%Y")

    if not DAILY_COMPLETED_TRADES:
        return (
            f"📊 **END OF DAY (EOD) PERFORMANCE REPORT ({today_str})**\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"• **Account Capital:** ₹{capital:,.2f}\n"
            f"• **Completed Signals:** 0\n"
            f"• **Status:** No trade setups reached target/SL or closed today yet."
        )

    total_trades = len(DAILY_COMPLETED_TRADES)
    wins = [t for t in DAILY_COMPLETED_TRADES if t["result"] == "WIN"]
    losses = [t for t in DAILY_COMPLETED_TRADES if t["result"] == "LOSS"]
    
    win_count = len(wins)
    loss_count = len(losses)
    win_rate = (win_count / total_trades) * 100 if total_trades > 0 else 0.0

    risk_per_trade = capital * 0.02
    reward_per_trade = risk_per_trade * 1.85

    gross_profit = (win_count * reward_per_trade) - (loss_count * risk_per_trade)
    brokerage_per_trade = 50.0
    total_brokerage = total_trades * brokerage_per_trade

    net_earnings = gross_profit - total_brokerage
    net_roi = (net_earnings / capital) * 100

    report = (
        f"📊 **END OF DAY (EOD) PERFORMANCE REPORT ({today_str})**\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 **Starting Account Capital:** **₹{capital:,.2f}**\n\n"
        f"📈 **DAILY SIGNAL PERFORMANCE:**\n"
        f"• **Total Trades Executed:** {total_trades}\n"
        f"• **Winning Signals:** {win_count} 🟢\n"
        f"• **Losing Signals:** {loss_count} 🔴\n"
        f"• **Overall Strategy Win Rate:** **{win_rate:.1f}%**\n\n"
        f"💵 **NET EVENING EARNINGS (AFTER TAX & BROKERAGE):**\n"
        f"• **Gross Profit:** ₹{gross_profit:,.2f}\n"
        f"• **Taxes & Brokerage (~₹50/trade):** -₹{total_brokerage:,.2f}\n"
        f"• 🏆 **NET TAKE-HOME EARNINGS:** **₹{net_earnings:,.2f}** ({net_roi:+.2f}% ROI)\n"
    )

    return report

# ==========================================
# 8. LIVE TRADE TRACKER
# ==========================================
def track_active_trades():
    global ACTIVE_SIGNALS, DAILY_COMPLETED_TRADES
    tz = pytz.timezone("Asia/Kolkata")
    now_dt = datetime.now(tz)

    for asset_name, trade in list(ACTIVE_SIGNALS.items()):
        if not trade or trade.get("status") != "OPEN":
            continue

        df = fetch_live_ohlc(asset_name)
        if df.empty:
            continue

        latest = df.iloc[-1]
        high_price = round(float(latest["high"]), 2)
        low_price = round(float(latest["low"]), 2)
        cmp = round(float(latest["close"]), 2)

        sig_type = trade["type"]
        sl, tp, option, entry = trade["sl"], trade["tp"], trade["option"], trade["entry_cmp"]

        if now_dt >= trade["expiry_dt"]:
            ACTIVE_SIGNALS[asset_name]["status"] = "CLOSED"
            pnl_pts = round(cmp - entry if sig_type == "BUY" else entry - cmp, 2)
            res = "WIN" if pnl_pts > 0 else "LOSS"
            DAILY_COMPLETED_TRADES.append({"asset": asset_name, "result": res})
            
            send_telegram_alert(
                f"⏳ **THETA EXPIRY EXIT:** `{asset_name}` ({option})\n"
                f"Closed at CMP ₹{cmp:,.2f} ({pnl_pts:+} pts)."
            )
            continue

        if sig_type == "BUY":
            if high_price >= tp or cmp >= tp:
                ACTIVE_SIGNALS[asset_name]["status"] = "CLOSED"
                DAILY_COMPLETED_TRADES.append({"asset": asset_name, "result": "WIN"})
                send_telegram_alert(
                    f"🎯 **TARGET ACHIEVED!** 🎉🟢\n"
                    f"• **Asset:** `{asset_name}` ({option})\n"
                    f"• **Entry:** ₹{entry:,.2f} ➔ **Target Hit:** ₹{tp:,.2f}\n"
                    f"• **Result:** **WIN SECURED!** 🚀"
                )
            elif low_price <= sl or cmp <= sl:
                ACTIVE_SIGNALS[asset_name]["status"] = "CLOSED"
                DAILY_COMPLETED_TRADES.append({"asset": asset_name, "result": "LOSS"})
                send_telegram_alert(
                    f"🛑 **STOP LOSS HIT** 🔴\n"
                    f"• **Asset:** `{asset_name}` ({option})\n"
                    f"• **Entry:** ₹{entry:,.2f} ➔ **SL Hit:** ₹{sl:,.2f}"
                )

        elif sig_type == "SELL":
            if low_price <= tp or cmp <= tp:
                ACTIVE_SIGNALS[asset_name]["status"] = "CLOSED"
                DAILY_COMPLETED_TRADES.append({"asset": asset_name, "result": "WIN"})
                send_telegram_alert(
                    f"🎯 **TARGET ACHIEVED!** 🎉🟢\n"
                    f"• **Asset:** `{asset_name}` ({option})\n"
                    f"• **Entry:** ₹{entry:,.2f} ➔ **Target Hit:** ₹{tp:,.2f}\n"
                    f"• **Result:** **WIN SECURED!** 🚀"
                )
            elif high_price >= sl or cmp >= sl:
                ACTIVE_SIGNALS[asset_name]["status"] = "CLOSED"
                DAILY_COMPLETED_TRADES.append({"asset": asset_name, "result": "LOSS"})
                send_telegram_alert(
                    f"🛑 **STOP LOSS HIT** 🔴\n"
                    f"• **Asset:** `{asset_name}` ({option})\n"
                    f"• **Entry:** ₹{entry:,.2f} ➔ **SL Hit:** ₹{sl:,.2f}"
                )

# ==========================================
# 9. BACKGROUND SCANNER THREAD
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
                alert_text = analyze_asset_scalp(asset, is_auto_scan=True)
                if alert_text:
                    send_telegram_alert(alert_text)
                time.sleep(2)

            if not STOP_ON_DEMAND_SIGNALS:
                for asset in ON_DEMAND_ASSETS:
                    alert_text = analyze_asset_scalp(asset, is_auto_scan=True)
                    if alert_text:
                        send_telegram_alert(alert_text)
                    time.sleep(2)

            track_active_trades()

        except Exception as e:
            logging.error(f"⚠️ Scanner Error: {e}")
        time.sleep(30)

# ==========================================
# 10. RENDER WEB SERVER
# ==========================================
app_flask = Flask(__name__)

@app_flask.route("/")
def home():
    return "🚀 Emerald Trade Agent Live!"

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app_flask.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# ==========================================
# 11. TELEGRAM HANDLERS & LISTENERS
# ==========================================
def get_main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📈 NIFTY", callback_data="ANALYZE_NIFTY"), InlineKeyboardButton("🏦 BANK NIFTY", callback_data="ANALYZE_BANK NIFTY"), InlineKeyboardButton("📊 SENSEX", callback_data="ANALYZE_SENSEX")],
        [InlineKeyboardButton("🎯 STOCKS", callback_data="TRIGGER_STOCKS"), InlineKeyboardButton("🔷 FINNIFTY", callback_data="ANALYZE_FINNIFTY"), InlineKeyboardButton("⚡ MIDCPNIFTY", callback_data="ANALYZE_MIDCPNIFTY")],
        [InlineKeyboardButton("🛢️ CRUDE OIL", callback_data="ANALYZE_CRUDE OIL"), InlineKeyboardButton("🔥 NATURAL GAS", callback_data="ANALYZE_NATURAL GAS")],
        [InlineKeyboardButton("🥇 GOLD", callback_data="ANALYZE_GOLD"), InlineKeyboardButton("🥈 SILVER", callback_data="ANALYZE_SILVER")],
        [InlineKeyboardButton("🛑 STOP SIGNALS", callback_data="STOP_SIGNALS"), InlineKeyboardButton("📊 EOD P&L REPORT", callback_data="EOD_REPORT")]
    ])

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome_text = (
        "🚀 *Institutional Trading Control Panel Online!*\n\n"
        "⚡ **QUICK TEXT COMMANDS:**\n"
        "• Type **`N`** $\\rightarrow$ NIFTY Live Price, Trend, Open, High, Low, & Prev. Close\n"
        "• Type **`S`** $\\rightarrow$ SENSEX Live Price, Trend, Open, High, Low, & Prev. Close\n"
        "• Type **`B`** $\\rightarrow$ BANK NIFTY Live Price, Trend, Open, High, Low, & Prev. Close\n\n"
        "🔴 *High-Conviction Signals (RRR > 1:2.0) display in Red Bold Alerts!*"
    )
    await update.message.reply_text(welcome_text, reply_markup=get_main_keyboard(), parse_mode="Markdown")

async def text_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().upper()
    if text == "N":
        summary = get_quick_market_summary("NIFTY")
        await update.message.reply_text(summary, parse_mode="Markdown", reply_markup=get_main_keyboard())
    elif text == "S":
        summary = get_quick_market_summary("SENSEX")
        await update.message.reply_text(summary, parse_mode="Markdown", reply_markup=get_main_keyboard())
    elif text == "B":
        summary = get_quick_market_summary("BANK NIFTY")
        await update.message.reply_text(summary, parse_mode="Markdown", reply_markup=get_main_keyboard())

async def eod_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    report = calculate_eod_performance(10000.0)
    await update.message.reply_text(report, reply_markup=get_main_keyboard(), parse_mode="Markdown")

async def button_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global STOP_ON_DEMAND_SIGNALS
    query = update.callback_query
    await query.answer()
    
    if query.data == "TRIGGER_STOCKS":
        await query.edit_message_text("🔍 Scanning top volume & momentum breakout stocks...", parse_mode="Markdown")
        report = scan_top_4_stocks()
        await context.bot.send_message(chat_id=query.message.chat_id, text=report, parse_mode="Markdown", reply_markup=get_main_keyboard())

    elif query.data == "STOP_SIGNALS":
        STOP_ON_DEMAND_SIGNALS = True
        msg = "🛑 **ON-DEMAND SIGNALS PAUSED!**"
        await context.bot.send_message(chat_id=query.message.chat_id, text=msg, parse_mode="Markdown", reply_markup=get_main_keyboard())

    elif query.data == "EOD_REPORT":
        report = calculate_eod_performance(10000.0)
        await context.bot.send_message(chat_id=query.message.chat_id, text=report, parse_mode="Markdown", reply_markup=get_main_keyboard())

    elif query.data.startswith("ANALYZE_"):
        asset = query.data.replace("ANALYZE_", "")
        STOP_ON_DEMAND_SIGNALS = False
        report = analyze_asset_scalp(asset, is_auto_scan=False)
        await context.bot.send_message(chat_id=query.message.chat_id, text=report, parse_mode="Markdown", reply_markup=get_main_keyboard())

# ==========================================
# 12. MAIN EXECUTION ENTRYPOINT
# ==========================================
if __name__ == "__main__":
    initialize_broker_apis()

    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=background_all_segment_scanner, daemon=True).start()

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("eod", eod_command))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), text_message_handler))
    app.add_handler(CallbackQueryHandler(button_callback_handler))

    logging.info("✅ Starting Telegram Bot...")
    app.run_polling(drop_pending_updates=True)
