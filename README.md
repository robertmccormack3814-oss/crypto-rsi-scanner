# Crypto RSI Scanner

Live crypto RSI(10) scanner modeled on the ASX RSI scanner.

## Strategy
- Daily candles are defined in UTC.
- Entry: coin is above its own SMA(200) and RSI(10) is below 30.
- Official exit: RSI(10) is above 40 on the daily scan, or the position reaches 10 completed daily candles.
- Intraday monitor: checks active signals every 30 minutes and sends a heads-up when provisional daily RSI(10) moves above 40. Intraday alerts do not close the official ledger trade.

## Pages
- `index.html` — live scanner dashboard
- `ledger.html` — completed-trade performance ledger

## Automation
- Daily official scan: shortly after 00:00 UTC.
- Intraday monitor: every 30 minutes, 24/7.

## Email secrets
Add these repository secrets in GitHub Actions:
- `SMTP_USERNAME`
- `SMTP_APP_PASSWORD`

The scanner is research tooling, not financial advice.
