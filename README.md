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

## v1.6.0 — real-time data fixes (delay / slow refresh)

- **Index price is now streamed.** The Upstox V3 WebSocket always includes the index (NIFTY 50 / Nifty Bank), even before the option chain loads. Each tick moves the "Last price" box and the forming candle on the chart (labelled `● LIVE tick`). Previously the price only changed when REST candles were re-downloaded.
- **REST fallback:** if no WebSocket tick arrives for 5 s, the page polls `GET /api/index-ltp` every 3 s (labelled `REST quote (WebSocket idle)`).
- **Auto refresh is selectable** (5 / 10 / 15 / 30 / 60 s, default 10 s, remembered in the browser). It was a fixed 60 s. Refreshes never overlap.
- **Faster, lighter refreshes:** the 10-day candle history is cached for 10 min on the server; only the small intraday call is repeated (cache 3 s, was 8 s). Option chain cache 4 s (was 12 s).
- **Polling intervals:** derivatives 6 s (was 15 s), option-chain table 10 s (was 30 s), paper-trade quotes 4 s (was 15 s). Near-ATM chain rows overlay live WebSocket LTP / OI.
- The data source is unchanged: Upstox is already a real-time source; no switch to Yahoo / NSE (both are delayed or blocked from cloud hosts).

## v1.7.0 — loss-analysis fixes (paper trades were entered against failing gates)

**Root causes found in v1.6.0**

1. The paper-trade **BUY button never looked at the signal gates**. A "WATCH PE" with two failing hard gates could still be bought.
2. The **option-volume ratio included the still-forming candle** (partial volume), so liquid contracts could read "dry", and a single block-trade bar could distort the baseline.
3. The **entry price was whatever LTP was fetched when the contract was selected** (could be minutes old), so stop and targets were measured from a stale entry.
4. Signals were calculated on the **forming candle**, so they could appear and vanish within one bar.
5. Premium-ATR stops could be ~30% of premium (ATR included the forming bar and overnight gaps), while the chosen target was about 1R, so reward:risk was ≈ 1:1 and needed a >50% win rate before costs.
6. No daily loss cap, no cooldown, no expiry-day cut-off, no trailing stop, no time stop.

**What changed**

- **Trade guard (paper trading panel)** — entries are blocked unless: signal is CONFIRMED on the same side as the contract; option volume is known and not dry; bid-ask spread within limit (when the feed provides it); data fresh (analysis ≤ 90 s, index tick ≤ 30 s); inside the time window (expiry-day cut-off 13:30 by default); one open trade at a time; max trades/day; daily loss cap; stop after N consecutive losses; cooldown; minimum reward:risk. Every rule is editable; "Enforce" can be switched off (then a confirmation lists the failing rules and the trade is tagged OVERRIDE).
- **Fresh fill at click time**: the app re-quotes and fills at the ask (or last price + slippage). Stop / time-stop / square-off exits are filled with slippage too.
- **Max stop = % of premium** (default 20%); targets keep the same R multiples. Premium ATR uses closed bars of the current session only.
- **Trailing stop** (breakeven at +1R, half of T1 gain locked after T1), **time stop** (default 30 min without 0.3R progress), **15:20 square-off**.
- **Closed candles only** for the confirmation engine (no repainting) and for the option-volume ratio; baseline uses today's session and caps block-trade spikes.
- **New hard gates**: *Not chasing* (≤ 1 ATR beyond EMA 9) and *Pullback-and-resume trigger* (price retested EMA 9 within 6 bars and the last closed bar resumes the trend). RSI overextension limits tightened to 70 / 30.
- **Feed**: level-1 bid/ask and day volume are parsed from the V3 `full` feed (`marketLevel.bidAskQuote`, `vtt`). If your account's feed does not include them, the spread rule shows a warning instead of blocking.
- **Learning loop**: each paper trade stores the signal, score, failing gates, option volume ratio, spread, index price at entry/exit, regime, higher-timeframe state, MFE/MAE and override flag; all are in the CSV export and an "Entry context" column in the history table.

These rules reduce low-quality entries; they do not make the strategy profitable. Collect 30–50 paper trades with the CSV before trusting any setting, and change one rule at a time.

## v1.7.1 — regime now detects ranges

The old regime said "BEARISH" whenever bear score > bull score and ADX ≥ 15. ADX (and Supertrend / higher timeframe / session VWAP) lag badly after a sharp move, so a market that had been flat for 25+ bars still read "bearish". The regime is now **SIDEWAYS** when ADX < 15 **or** the last 20 closed bars show range behaviour: Kaufman efficiency ratio < 0.30 with the whole range ≤ 6 ATR, or ≥ 3 EMA 9/21 crosses. The card shows the range, its size in ATR, the efficiency and the prior bias; the "Not sideways" hard gate uses it, so entries stay blocked until a close outside the range. Thresholds (20 bars, 0.30, 6 ATR, 3 crosses) are starting values — tune them on your own paper-trade CSV.
