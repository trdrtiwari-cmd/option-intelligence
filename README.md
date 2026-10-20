# NSE Trend Confirmation

Hindi-first research dashboard for NIFTY 50 and BANK NIFTY, powered by the Upstox market-data API (read-only; it never places orders).

## What it checks
1. Advance–Decline ratio (index constituents, vs previous close)
2. Sector Heat Map (sector indices, vs previous close)
3. CE/PE premium % change (nearest ATM pair, vs previous close)
4. CE/PE premium graph (5-minute option candles, session + recent trend)
5. Index price direction (5-minute index candles, EMA-9 + move from open)

Final output is `BUY CE`, `BUY PE` or `WAIT`. A directional signal is produced only when the market is open (09:15–15:30 IST, Mon–Fri), the latest index candle is fresh, and **all five** checks agree. Anything missing, stale or closed gives `WAIT`. When the market is closed the dashboard still shows the last session's data for reference, labelled "MARKET CLOSED".

## Required setting: UPSTOX_ACCESS_TOKEN
Without it every check shows "डेटा उपलब्ध नहीं" and the pill reads `TOKEN NOT SET`.
Upstox access tokens expire daily (around 3:30 AM IST), so generate a new one each trading day and update the variable.

- Render: Dashboard → your service → Environment → set `UPSTOX_ACCESS_TOKEN` → Save (it redeploys).
- Local (Windows): `set UPSTOX_ACCESS_TOKEN=your_token` before starting uvicorn.

`GET /api/health` reports `token_configured` so you can verify the variable is picked up.

## Run locally
```bat
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
set UPSTOX_ACCESS_TOKEN=your_token
uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000

## Tests (offline, no token needed)
```bat
python -m unittest discover -s tests -v
```

## Deploy on Render
Push this folder to the repository root; Render reads `render.yaml`. Set `UPSTOX_ACCESS_TOKEN` as above. The free plan may sleep when idle.

## Endpoints
- `GET /` dashboard
- `GET /api/health`
- `GET /api/snapshot/{NIFTY|BANKNIFTY}`
- `GET /api/options/{NIFTY|BANKNIFTY}?moneyness=ATM|ITM|OTM&expiry_date=YYYY-MM-DD`

## Maintenance
- Update the constituent lists in `app/universe.py` after each NSE rebalance (March / September). Unknown symbols are skipped; the A/D check needs ≥80% coverage.
- Exchange holidays are not checked; on a holiday the last session's candles show as `STALE`/`MARKET_CLOSED`.

This is a transparent decision-support prototype, not a proven strategy. Paper-trade before risking money.
