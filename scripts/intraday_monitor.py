import os
import smtplib
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
    rsi = (100.0 - (100.0/(1.0+rs))).astype("float64")
    rsi.loc[(avg_loss==0)&(avg_gain>0)] = 100.0
    rsi.loc[(avg_gain==0)&(avg_loss>0)] = 0.0
    rsi.loc[(avg_gain==0)&(avg_loss==0)] = 50.0
    return rsi


def send_email(subject, body):
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_APP_PASSWORD", "").strip()
    if not username or not password:
        return
    msg = EmailMessage(); msg["From"] = username; msg["To"] = CONFIG["alert_email"]; msg["Subject"] = subject; msg.set_content(body)
    with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as s:
        s.starttls(); s.login(username,password); s.send_message(msg)


def main():
    state = load_json(DATA/"state.json", {"active_signals":{}})
    active = state.get("active_signals", {})
    alert_state = load_json(DATA/"intraday_state.json", {"alerted":{}})
    alerted = alert_state.get("alerted", {})

    for sym, sig in active.items():
        ticker = sig["ticker"]
        try:
            daily = yf.download(ticker, period="18mo", interval="1d", auto_adjust=False, progress=False)
            intraday = yf.download(ticker, period="1d", interval="5m", auto_adjust=False, progress=False)
            if daily.empty or intraday.empty:
                continue
            dc = daily["Close"]; ic = intraday["Close"]
            if isinstance(dc,pd.DataFrame): dc=dc.iloc[:,0]
            if isinstance(ic,pd.DataFrame): ic=ic.iloc[:,0]
            dc = pd.to_numeric(dc, errors="coerce").dropna()
            ic = pd.to_numeric(ic, errors="coerce").dropna()
            latest_price = float(ic.iloc[-1])
            today = pd.Timestamp.utcnow().date()
            prior = dc[pd.Index([x.date() for x in dc.index]) < today]
            provisional = pd.concat([prior, pd.Series([latest_price], index=[pd.Timestamp.utcnow()])])
            prsi = float(rsi_wilder(provisional, CONFIG["rsi_period"]).dropna().iloc[-1])
        except Exception as e:
            print(sym, e); continue

        key = sig.get("entry_date","")
        already = alerted.get(sym)==key
        if prsi > CONFIG["exit_rsi_above"] and not already:
            entry = float(sig["entry_price"])
            gain = ((latest_price-entry)/entry)*100 if entry else 0.0
            send_email(f"CRYPTO RSI INTRADAY >40: {sym}", f"{sig['company']} ({sym}) provisional daily RSI(10) is above 40.\n\nProvisional RSI(10): {prsi:.2f}\nLatest price: ${latest_price:.6f}\nEntry price: ${entry:.6f}\nMove from entry: {gain:+.2f}%\n\nHeads-up only. The official ledger exit remains the next completed daily scan above RSI 40.\n")
            alerted[sym]=key
        elif prsi <= CONFIG["exit_rsi_above"] and already:
            alerted.pop(sym,None)

    active_symbols=set(active)
    alerted={k:v for k,v in alerted.items() if k in active_symbols}
    save_json(DATA/"intraday_state.json", {"alerted":alerted,"updated_at":now_iso()})


if __name__=="__main__":
    main()
