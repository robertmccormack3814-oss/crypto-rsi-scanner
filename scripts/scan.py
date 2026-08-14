import os
import smtplib
import time
from email.message import EmailMessage

import numpy as np
import pandas as pd
import yfinance as yf

from common import DATA, CONFIG, load_json, save_json, now_iso


def rsi_wilder(close, period=10):
    close = pd.to_numeric(close, errors="coerce").dropna()
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1/period, adjust=False, min_periods=period).mean()
    safe_loss = avg_loss.where(avg_loss != 0, np.nan)
    rs = avg_gain / safe_loss
    rsi = (100.0 - (100.0 / (1.0 + rs))).astype("float64")
    rsi.loc[(avg_loss == 0) & (avg_gain > 0)] = 100.0
    rsi.loc[(avg_gain == 0) & (avg_loss > 0)] = 0.0
    rsi.loc[(avg_gain == 0) & (avg_loss == 0)] = 50.0
    return rsi


def send_email(subject, body):
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_APP_PASSWORD", "").strip()
    if not username or not password:
        print("SMTP secrets missing; email skipped.")
        return
    msg = EmailMessage()
    msg["From"] = username
    msg["To"] = CONFIG["alert_email"]
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
        server.starttls()
        server.login(username, password)
        server.send_message(msg)


def trade_key(t):
    return (t.get("symbol"), t.get("entry_date"), t.get("exit_date"))


def holding_days(hist, entry_date):
    entry = pd.Timestamp(entry_date).date()
    return int(sum(ts.date() > entry for ts in hist.index))


def main():
    state = load_json(DATA / "state.json", {"active_signals": {}, "completed_trades": []})
    active = state.get("active_signals", {})
    completed = state.get("completed_trades", [])
    completed_keys = {trade_key(t) for t in completed}

    rows, entries, exits = [], [], []
    errors = 0

    for coin in CONFIG["coins"]:
        sym, name, ticker = coin["symbol"], coin["name"], coin["ticker"]
        try:
            hist = yf.download(ticker, period="2y", interval="1d", auto_adjust=False, progress=False)
            if hist.empty:
                raise RuntimeError("no history")
            close = hist["Close"]
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            close = pd.to_numeric(close, errors="coerce").dropna()
            if len(close) < CONFIG["sma_period"] + 2:
                raise RuntimeError("insufficient history")
            rsi = rsi_wilder(close, CONFIG["rsi_period"]).dropna()
            last_close = float(close.iloc[-1])
            last_rsi = float(rsi.iloc[-1])
            sma200 = float(close.rolling(CONFIG["sma_period"]).mean().iloc[-1])
            above = last_close > sma200
            d = str(close.index[-1].date())
        except Exception as e:
            print(sym, e)
            errors += 1
            continue

        row = {"symbol":sym,"company":name,"ticker":ticker,"date":d,"price":last_close,"rsi10":last_rsi,"sma200":sma200,"above_sma200":above,"active":sym in active}

        if sym in active:
            sig = active[sym]
            held = holding_days(pd.DataFrame(index=close.index), sig["entry_date"])
            exit_reason = None
            if last_rsi > CONFIG["exit_rsi_above"]:
                exit_reason = f"RSI(10) rose above {CONFIG['exit_rsi_above']}"
            elif held >= CONFIG["max_holding_days"]:
                exit_reason = f"{CONFIG['max_holding_days']}-day time exit"

            if exit_reason:
                entry_price = float(sig["entry_price"])
                gain_pct = ((last_close-entry_price)/entry_price)*100 if entry_price else 0.0
                exit_row = {**sig,"exit_date":d,"exit_price":last_close,"exit_rsi10":last_rsi,"holding_trading_days":held,"exit_reason":exit_reason,"gain_pct":gain_pct}
                exits.append(exit_row)
                key = trade_key(exit_row)
                if key not in completed_keys:
                    completed.append(exit_row); completed_keys.add(key)
                send_email(f"CRYPTO RSI EXIT: {sym} — {exit_reason}", f"{name} ({sym}) exit signal.\n\nExit reason: {exit_reason}\nExit price: ${last_close:.6f}\nGain from entry: {gain_pct:+.2f}%\nRSI(10): {last_rsi:.2f}\nEntry date: {sig['entry_date']}\nEntry price: ${entry_price:.6f}\nHolding days: {held}\n")
                del active[sym]
                row["active"] = False
            else:
                sig["holding_trading_days"] = held
                sig["latest_price"] = last_close
                sig["latest_rsi10"] = last_rsi

        if sym not in active and above and last_rsi < CONFIG["entry_rsi_below"]:
            sig = {"symbol":sym,"company":name,"ticker":ticker,"entry_date":d,"entry_price":last_close,"entry_rsi10":last_rsi,"entry_sma200":sma200,"holding_trading_days":0,"latest_price":last_close,"latest_rsi10":last_rsi}
            active[sym] = sig
            entries.append(sig)
            row["active"] = True
            send_email(f"CRYPTO RSI ENTRY: {sym} RSI(10) {last_rsi:.1f}", f"{name} ({sym}) entry signal.\n\nPrice: ${last_close:.6f}\nSMA(200): ${sma200:.6f}\nRSI(10): {last_rsi:.2f}\n\nEntry: above own SMA(200) and RSI(10) below {CONFIG['entry_rsi_below']}.\nExit: RSI(10) above {CONFIG['exit_rsi_above']} or after {CONFIG['max_holding_days']} daily candles.\n")

        rows.append(row)
        time.sleep(0.15)

    rsi_exits = sum(str(t.get("exit_reason","")).startswith("RSI(10) rose above") for t in completed)
    time_exits = sum("time exit" in str(t.get("exit_reason","")).lower() for t in completed)
    resolved = len(completed)

    state = {"active_signals":active,"completed_trades":completed,"updated_at":now_iso()}
    save_json(DATA / "state.json", state)
    dashboard = {
      "generated_at": now_iso(),
      "stats": {"universe":len(CONFIG["coins"]),"scanned":len(rows),"entries_today":len(entries),"exits_today":len(exits),"active":len(active),"errors":errors,"completed_trades":resolved,"rsi_exits":rsi_exits,"time_exits":time_exits,"rsi_exit_rate_pct":round(rsi_exits/resolved*100,1) if resolved else None},
      "entries_today":entries,"exits_today":exits,"completed_trades":completed,
      "active_signals":sorted(active.values(), key=lambda x:x["symbol"]),
      "stocks":sorted(rows, key=lambda x:x["rsi10"])
    }
    save_json(DATA / "scanner.json", dashboard)
    print(dashboard["stats"])


if __name__ == "__main__":
    main()
