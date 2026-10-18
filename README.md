# Option Intelligence — Upstox Live Derivatives Bridge

A FastAPI website for NIFTY 50 / BANK NIFTY research and paper-trading. It uses Upstox REST APIs for option-chain snapshots and the official Upstox Python SDK's Market Data Feed V3 WebSocket for subscribed near-ATM option contracts.

## What this build includes

- NIFTY 50 / BANK NIFTY candle chart (historical + today's intraday candles merged) and technical analysis with TradingView-compatible RSI/ATR (Wilder) and a session-anchored VWAP.
- Option chain for a real expiry date. `current_week` / `current_month` are resolved to actual dates from the contract list, so PCR/OI and the chain table always describe the same expiry.
- Aggregate PCR calculated from total put OI / total call OI for the same chain expiry.
- Call/put OI totals and average chain IV from the latest REST option-chain snapshot.
- Upstox Market Data Feed V3 WebSocket subscription for a bounded near-ATM set of option contracts; live LTP/OI/IV ticks are displayed per contract when the feed returns them.
- Backend WebSocket `/ws/live?symbol=NIFTY` or `BANKNIFTY` broadcasts decoded tick updates to the browser.
- Freshness labels: only a received contract tick <=15 seconds old is labelled fresh/live; older or absent values are marked stale/unavailable.
- Optional authenticated TradingView webhook receiver. This receives Pine alerts only; it does not inject external API values into Pine Script or place orders.
- No broker orders, no automatic execution.

## Data semantics — read carefully

1. **WebSocket live:** per-contract LTP/OI/IV only for contracts subscribed around the underlying's ATM strike. The default subscription is limited to the 12 nearest strikes (calls and puts) to keep it bounded.
2. **REST snapshot:** aggregate PCR, total call OI, total put OI and average IV are calculated from the option-chain response. These are snapshot values, not a continuous WebSocket aggregate. The UI labels them as REST snapshots.
3. **Freshness:** a live tick is fresh only when its backend receipt age is <=15 seconds. Market closures, token expiry, disconnections, API permission errors or missing fields may lead to `UNAVAILABLE`; the app does not invent values.
4. **PCR definition:** the displayed aggregate PCR is `sum(put OI) / sum(call OI)` over rows returned for the selected expiry. This is distinct from any per-row PCR returned by the provider.
5. **IV normalization:** the WebSocket IV is normalized to percentage if the SDK returns a fractional value. Confirm the returned scale with live sample data in your Upstox account before using it in a strategy.
6. **TradingView limitation:** Pine Script cannot directly call arbitrary Upstox REST endpoints or receive an arbitrary response from this backend. TradingView webhooks are one-way alert delivery (TradingView → website). To use external OI/PCR/IV inside Pine itself requires a supported TradingView data source or another compatible instrument/feed; this project does not pretend the webhook makes that possible.
7. **No performance guarantee:** this is a data bridge and research dashboard, not a validated profitable system. Option-chain and WebSocket availability depend on Upstox account permissions, API policies, rate limits, access token validity, market hours and current provider API behavior.

## Run locally

```bash
python -m venv .venv
# Windows: .venv\\Scripts\\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Copy `.env.example` values into environment variables. Never paste API secrets into browser code or commit them to GitHub.

## Deploy on Render

1. Upload this folder to a **new private GitHub repository**.
2. Create a Render Web Service connected to that repository.
3. Build command: `pip install -r requirements.txt`
4. Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
5. Set Render environment variables:
   - `UPSTOX_API_KEY`
   - `UPSTOX_API_SECRET`
   - `UPSTOX_REDIRECT_URI=https://YOUR-SERVICE.onrender.com/auth/callback`
   - `UPSTOX_ACCESS_TOKEN` (optional quick-start; replace it as required by Upstox)
   - `DASHBOARD_PASSWORD` (recommended: browser asks for it; username can be anything)
   - `TRADINGVIEW_WEBHOOK_SECRET` (optional, long random value; enables protected webhook endpoint)
6. Register the exact same redirect URI in the Upstox developer app, redeploy, then use **Connect Upstox**.
7. Check `/api/status`, then load live derivatives during market hours.

### Token note

Upstox access tokens expire every day (early morning IST). The header pill shows `Upstox token valid` / `expired`; when expired, click **Connect Upstox**. OAuth callback stores the access token in process memory for this starter build. A restart/redeploy loses it. For production, use a secure durable token/session mechanism consistent with Upstox's current authentication policy. Do not store secrets in frontend JavaScript or public GitHub files.

## API endpoints

- `GET /api/status`
- `GET /api/candles?symbol=NIFTY&interval=5`
- `GET /api/option-contracts?symbol=NIFTY&expiry_date=current_week`
- `GET /api/option-chain?symbol=NIFTY&expiry_date=current_week`
- `GET /api/live-derivatives?symbol=NIFTY&expiry_date=current_week`
- `WS /ws/live?symbol=NIFTY`
- `POST /api/tradingview/webhook` (requires configured `TRADINGVIEW_WEBHOOK_SECRET`)
- `GET /api/tradingview/last-alert`

Use `BANKNIFTY` for Bank Nifty. Relative expiry keywords such as `current_week` and `current_month` are passed to Upstox; actual available expiry is reported in the response.

## Testing checklist

- Verify `/api/status` reports token configured.
- Open the site during market hours and select NIFTY / BANK NIFTY.
- Verify expiry and snapshot timestamp, then confirm the stream status becomes `LIVE TICKS` and individual rows have fresh tick ages.
- If the UI says `UNAVAILABLE`, inspect server logs and confirm the access token, SDK installation, subscription permission, instrument keys and Upstox API status. Do not treat REST snapshot as a WebSocket tick.
- Paper-test before making any decisions. This application places no orders.


## Optional TradingView → website alerts

Create a TradingView alert with webhook URL `https://YOUR-APP.onrender.com/api/tradingview/webhook` and a JSON message such as:

```json
{"secret": "YOUR_TRADINGVIEW_WEBHOOK_SECRET", "symbol": "{{ticker}}", "signal": "BUY_CE", "price": {{close}}}
```

The latest alert is available at `GET /api/tradingview/last-alert`. (The webhook path is exempt from `DASHBOARD_PASSWORD`; it is protected by its own secret.)

## Revision notes (fixes in this version)

- **Decision was stuck on WAIT.** Index candles have zero volume, so VWAP equalled the close and its 15 points could never be scored (max 60 vs. the 75 threshold). Scoring is now normalised to the points actually available, VWAP falls back to a labelled session average price when there is no volume, and the bias threshold is 70% of available points.
- **Live ticks showed `—` for type/strike/OI/IV.** OI/IV are read from the V3 `marketFF` block (not the old V2 `eFeedDetails`), rows are enriched with contract metadata, and each row shows its own tick age.
- **PCR/OI and the option chain could be for different expiries.** The chain endpoint only accepts real dates; keywords are now resolved server-side and the page loads the expiry list first.
- **Today's candles were missing during market hours.** The historical endpoint excludes the current session; intraday candles are merged in. Candles are explicitly sorted oldest→newest.
- **RSI/ATR now use Wilder smoothing** (match TradingView). Premium ATR for paper trades uses the same function.
- **Chart no longer re-fits all 5 days on every refresh**; it opens on the latest ~1.6 sessions and keeps your zoom. `Reset view` button added.
- **Option chain** opens on ±10 strikes around ATM with the ATM row highlighted, refreshes every 30 s while the market is open, and shows the lot size.
- **Paper trading:** default ATR multipliers give 1.5R at Target 1 (was 0.67R), quantity defaults to one lot, stop-loss gaps fill at the observed quote, label bug after switching underlying fixed.
- **Reliability:** one WebSocket per page (no reconnect storms), shared caches for chain/contracts/candles (fewer Upstox calls, fewer 429s), token validity and market-open status shown, optional password gate, constant-time webhook secret check.

## Support / resistance map and signal confirmation (latest revision)

**S/R engine** (`computeSR` in `static/app.js`): levels are *zones*, not single prices. Candidates are 7-bar swing pivots over the last ~5 sessions plus previous-day high / low / close and today's high / low. Candidates within ~0.6 ATR are merged and scored by touches, key-level status and recency (Weak / Medium / Strong). The nearest three zones per side are drawn on the chart as lines that start where the level formed (heavier line = stronger level) and are excluded from auto-scaling, so far-away levels no longer stretch the price axis. Weak single-touch zones only fill a slot when fewer than three stronger zones exist on that side. The *S/R map* panel lists price, zone range, distance (points, %, ATR multiples), touches, strength and basis, and can take its levels from the chart timeframe, 15 min or 30 min candles.

**Confirmation engine** (`indicators.py`): nine weighted checks (EMA 9/21, Supertrend 10/3, session VWAP, MACD 12/26/9, RSI, ADX/DI, 11-bar breakout, volume where available, 15-minute higher-timeframe alignment), plus Bollinger %B / squeeze as context. A signal is **CONFIRMED** only when every hard gate passes: score >= 70, ADX >= 20 with DI agreeing, Supertrend agrees, higher timeframe agrees, not sideways (ADX >= 15), RSI not overextended (< 80 CE / > 20 PE) and at least 1 ATR of room to the next opposing S/R level. If one or two gates fail it shows WATCH with the missing gate; otherwise WAIT.

This lowers false signals but does not guarantee profit. Validate the rules on your own data before risking money.

## Updating a deployment

Static files are now served with `Cache-Control: no-cache` and versioned URLs (`app.js?v=…`), so a redeploy reaches the browser on the next load. The footer shows `page vX · server vY`; if they differ, a banner asks you to press Ctrl+F5.

## Volume in the signal

The index has no volume (Upstox sends zero for indices), so the weighted score excludes the volume check. The **selected option's** volume is used instead: its last 3 bars vs the prior 20 become a check row (Expanding >= 1.2x, Normal, Dry < 0.6x). When the selected contract matches the signal side (CE for a CE signal), "Option volume not dry (>= 0.6x)" is also a hard gate, because thin volume means wide spreads and poor fills. The room-to-S/R gate always uses the **index** levels, whichever chart you are viewing, and the index analysis refreshes in both chart views.

## Index volume from the front-month future

Indices have no volume, so the app borrows the volume of the front-month index future (NIFTY / BANKNIFTY) for the index candles. This feeds the session VWAP weights, the relative-volume reading and the "Volume expansion" check in the confirmation engine. The future is found from Upstox's public instrument file (`assets.upstox.com/.../NSE.json.gz`), which is **streamed and filtered** rather than loaded into memory, downloaded at most once per IST day, and rolled to the next month within 3 days of expiry. Futures candles are matched to index candles by timestamp; if fewer than half line up, or the volume is zero, no volume is attached rather than wrong volume. If the download fails it is retried after 15 minutes and everything falls back to the previous behaviour (volume check excluded). `/api/status` shows `futures_volume.ready` / `error`. Option volume remains a separate gate.
