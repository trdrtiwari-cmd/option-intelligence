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

## Data Check dashboard (v1.8) — open `/dashboard`

A separate page that turns five market checks into one signal table plus detail cards:

| Data check | How the signal is decided |
|---|---|
| Advance / decline ratio | Stocks up vs down from the selected universe (Nifty 50 / 100 / 500 / Bank). Ratio ≥ 1.5 bullish, ≤ 0.67 bearish. |
| Sector heat map | 12 major sector indices, % change vs previous close. Bullish/bearish when ≥ 2/3 of sectors are green/red. |
| Option premium % change | ATM CE and PE % change vs previous close. CE up & PE down by ≥ 3 points = bullish (reverse = bearish). Both up = volatility spike, both down = premium decay (caution). |
| Option premium graph | CE and PE premium candles for the session. Each leg is RISING / FALLING / FLAT (close vs 9-EMA and 3 bars ago). |
| Index price Up / Mid / Down | Five ±1 checks: opening range, session average price, previous close, day open, previous-day high/low. Score ≥ +3 UP, ≤ −3 DOWN, else MID. |

An overall confluence line counts the bullish / bearish checks. All thresholds are in `THRESHOLDS` at the top of `dashboard_data.py`.
Endpoints: `GET /dashboard`, `GET /api/dashboard/summary`, `GET /api/dashboard/premium-graph`.
Breadth uses NSE's own constituent CSV (so index rebalances are picked up automatically); if it cannot be downloaded, Nifty 50 falls back to a built-in list that may be outdated. Unit tests: `python tests/test_dashboard_data.py`.

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
   - `DATABASE_URL` (PostgreSQL connection string — required for accounts to survive restarts) and `SIGNUP_CODE` (private invite code for creating accounts); see the v1.10.0 section
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

The latest alert is available at `GET /api/tradingview/last-alert`. (The webhook path is exempt from the login; it is protected by its own secret.)

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

## v1.8.2 — regime no longer calls a downtrend "sideways"

**Bug:** the v1.7.1 range rule treated **≥ 3 EMA 9/21 crosses** (or ADX < 15) as SIDEWAYS regardless of what price was doing. After a spike and rounded top the EMAs cross several times, then price breaks down — the card kept saying SIDEWAYS ("7.0 ATR · efficiency 0.49 · 3 EMA crosses") while NIFTY was making new lows. ADX lags, so it could not rescue the call.

**Fix (`indicators.py`):**
- New `directional_structure()` runs *before* the range test. It needs the EMA 9/21 + close on the same side of EMA 21, one hard proof (a sustained move that is still running — efficiency ≥ 0.35 and ≥ 2 ATR over 20 bars with ≥ 1 ATR in the last 8 — or a sharp ≥ 2.5 ATR burst in 8 bars, or a close beyond the prior 20-bar range) **plus** one more confirmation (lower highs/lows or higher highs/lows, or price on the right side of VWAP with EMA 21 sloping the same way).
- If that is met the regime is **DOWNTREND / UPTREND** (phase `BREAKDOWN` / `BREAKOUT` / `TREND`) and the "Not sideways" gate passes. The card lists the evidence and warns when ADX is still lagging.
- SIDEWAYS now needs *no* directional proof: (efficiency < 0.30 and range ≤ 6 ATR) or (≥ 3 EMA crosses **and** efficiency < 0.40), or ADX < 15.
- EMA crosses are counted with a 0.1 ATR dead band so noise wiggles are not crosses.
- API adds `regime_label`, `regime_phase`, `trend_evidence`, `range.broke_out`. `regime` keeps BULLISH / BEARISH / SIDEWAYS so colours and the paper-trade CSV are unchanged.

The ADX ≥ 20, Supertrend and higher-timeframe gates are untouched, so an early breakdown can show DOWNTREND while the decision still says WATCH/WAIT until those lagging gates agree. That is intentional protection; relax them only after reviewing your paper-trade CSV. Tests: `python tests/test_regime.py`.

## v1.9.0 — signal time + Data Check dashboard in the final signal

**1. Every signal now shows when it was generated.** The Signal confirmation panel has a *Signal generated* box: generation time (IST, the close of the candle where the signal began), age in minutes/bars, price at the signal vs now (in ATR) and whether the entry window is OPEN, LATE or EXPIRED. The Decision card also shows the time. The technical start time is found by **replaying `analyze()` on the closed candles of the same session** and walking back while the signal is unchanged, so it survives server restarts and never repaints. The dashboard side has no candle history, so the server remembers when it last changed; after a restart that time is marked *est.* and the **older** (more conservative) technical time is used.

**2. The Data Check dashboard now votes on the final signal.** `/api/analysis` fetches the dashboard's five checks (breadth, sectors, option premium %, premium graph, index zone → `final_signal`), cached 8 s, and adds three hard gates after the technical ones:

| Gate | Passes when |
|---|---|
| Data Check dashboard agrees | dashboard side = terminal side and level is CONFIRMED or BIAS (unavailable data = fail) |
| Entry time window | market open and 09:30 ≤ IST time < 15:00 |
| Signal fresh | age ≤ 3 candles (15 min on 5m) **and** price moved ≤ 1 ATR from the signal price; > 12 candles = EXPIRED |

Results: **CONFIRMED** only when the terminal's gates, the dashboard, the time window and freshness all pass. Terminal confirmed but dashboard opposite → **CONFLICT**. Terminal confirmed but too old / price already ran → **STALE … don't chase**. A dashboard read alone never creates an entry. The paper-trade guard also blocks entries on a non-fresh signal, and every paper trade stores `ctx_signal_at`, `ctx_signal_age_min` and `ctx_dash` in the CSV so you can see which entry times lose money.

New API fields on `/api/analysis`: `final`, `timing`, `dashboard`, `signal_technical`; `decision` now holds the fused action. If the dashboard cannot be loaded the signal is capped at WATCH (fail-safe), and a fusion error never breaks the analysis. All rules are constants at the top of `signal_fusion.py` (`FRESH_BARS`, `EXPIRE_BARS`, `MAX_CHASE_ATR`, `ENTRY_START_MIN`, `ENTRY_CUTOFF_MIN`) — tune them on your paper-trade CSV. Tests: `python tests/test_fusion.py`.

### v1.9.1 — signal time on the Data Check dashboard
The dashboard's own final signal now shows *Signal since HH:MM:SS IST · N min ago* plus the index price when it last changed, and every row of the score breakdown shows when that check last changed (`*` = estimated: the server started watching after the change, so it may be older). The main terminal and `/dashboard` share the same tracker, so both pages show the same time. The dashboard has no candle history to replay, so these times are the moment this server first saw the state change — with the page closed and nothing polling, changes are not seen.

## v1.9.1 — Scalp mode (20–40 point moves)

New **Signal mode** selector in the toolbar (saved in the browser; default **Scalp**). `Standard` keeps the original strict rules unchanged.
Why: on a fast fall (price ~3 ATR under EMA 9, RSI in the 20s, no retest of EMA 9) the Standard gates "Not chasing", "RSI > 30" and
"Pullback-and-resume" all fail, so a trending day produced no entry at all.

| Rule | Standard | Scalp |
|---|---|---|
| Entry trigger | EMA 9 retest that resumes | EMA 9 retest **or** a pause of 1–3 counter-trend candles (within the last 3 bars) followed by a real candle closing beyond the pause. A straight run with no pause is **not** an entry |
| RSI limit | > 30 (PE) / < 70 (CE) | > 18 / < 82, only when price structure confirms the trend |
| Chase limit | ≤ 1 ATR beyond EMA 9 | ≤ 3.5 ATR beyond EMA 9, and the last bar is not a blow-off (range ≤ 2.5 ATR) |
| ADX / Supertrend | must agree | must agree **or** price structure confirms the trend (they lag) |
| Higher timeframe | must agree | must not be against |
| Index room to S1 / R1 | ≥ 1 ATR | ≥ 0.5 ATR (≈ 20 pts); passes when there is no level |
| Signal freshness | ≤ 3 bars old, ≤ 1.0 ATR moved | ≤ 2 bars old, ≤ 0.6 ATR moved (expires after 6 bars) |

Unchanged in both modes: score ≥ 70, not sideways, Data Check dashboard must agree, 09:30–15:00 IST entry window, option-volume gate.
Rules live in `MODES` (indicators.py). Paper trades record the mode (`ctx_mode`) so you can compare the two in the CSV.
API: `GET /api/analysis?symbol=NIFTY&interval=5&mode=scalp|standard`. Tests: `python tests/test_scalp.py`.


## v1.10.0 — accounts, login and automatic saving

**What changed.** The site now has sign-up, log-in and log-out. Everything except `/health`, `/login` and the TradingView webhook
needs a login. Your paper trades, paper capital, risk-guard settings and preferences (signal mode, refresh rates) are saved to
your account automatically a moment after every change, and are loaded on any device you log in from. The old shared
`DASHBOARD_PASSWORD` browser pop-up is gone.

**Required on Render: a database.** Render's free web service has no permanent disk, so a local file would lose every account on
each restart/deploy. Create a free PostgreSQL database with any provider (for example Neon or Supabase — check their current free
limits; Render's own PostgreSQL also works) and put its connection string into the `DATABASE_URL` environment variable. Tables are created
automatically on first start. Without `DATABASE_URL` the app uses a SQLite file (`data/option_intelligence.db`) — fine for running on your own computer.

**Who can sign up.**
* Set `SIGNUP_CODE` on Render to a long private phrase. People create an account with that invite code. (If you leave it empty but still have the old `DASHBOARD_PASSWORD` set, that is used as the code.)
* If neither is set, only the very first account can be created — it becomes the **owner** — and sign-ups then close. Do the first sign-up yourself immediately after deploying.
* Only the owner can use **Connect Upstox** (the market-data token is shared by everyone who logs in).

**Pages / buttons.** `/login` (log in or create account), `/account` (change password, download your data as JSON, delete account), and a header chip on every page showing *Saved ✓ 10:32* (or *Saving…* / *Offline — unsaved, will retry*), your username and **Log out**.

**How saving works.** `static/sync.js` runs before the app, loads your saved data into the browser, and then watches the same browser storage the app already uses — no app code changed. Changes are uploaded after ~1.5 s (price-only refreshes of open paper trades every 30 s), when you hide/close the tab, and when you log out. If the connection drops, changes stay on the device and are retried. Two devices editing at once are merged (paper trades by trade id; a closed trade beats an open copy). Data that was in your browser before accounts existed is uploaded to the first account that signs in.

**Security notes.** Passwords are hashed with scrypt; sessions are random tokens (only a hash is stored) in an HttpOnly, SameSite cookie, valid 30 days of activity; login attempts are rate-limited; cross-site write requests are refused. There is no email, so a forgotten password is reset by the owner: `DATABASE_URL=... python manage_users.py reset-password NAME` (also `list`, `make-admin`, `delete`).

**Tests.** `python tests/test_accounts.py` (accounts, sessions, saved data, login middleware) and `node tests/sync/run.js` (the auto-save script against the real storage code: migration, two devices, outage, shared browser, logout).


## v1.11.0 — Signal diagnostics (`/diagnostics`)

Answers "where do the corrections need to be?" from your own market history instead of opinion. It replays the technical engine
bar by bar over the last N days (using only candles that had closed at each moment), then measures what the index did after every signal:
win rate and points per trade after your cost allowance; an **exit grid** (target × stop) showing which exit rule fits the signals;
breakdowns by time of day, side, regime, score, RSI, stretch beyond EMA 9, entry trigger and day; and a **gate check** — setups blocked by
exactly one gate — showing which gate is too strict and which is earning its keep. Every run is compared with **random entries using the
same exit**, because a signal only has an edge to the extent it beats random. Findings marked CORRECTION / WORKS / PROBLEM in plain language; the full report can be downloaded as JSON.

Limits: technical engine only (the Data Check dashboard has no history to replay); results are in index points, not option rupees; use 15–20 days
and confirm anything you change on a second, different period. CLI: `python diagnostics.py candles.json scalp 5 4` (candles.json = the `candles` array from `/api/candles`).
Tests: `python tests/test_diagnostics.py`.


## v1.11.1 — corrections from the first diagnostics run (NIFTY, 5 m, 14 days, 2026-10-05 → 10-23)

**What the numbers said.** Standard mode (55 signals) was indistinguishable from random entries. Scalp mode (117 signals) beat random entries by +6.3 pts/trade (statistically real) but only made +1.4 pts/trade at the old 30/20 exit. Every cell of the 60-point-target row of the scalp grid was profitable (+3.7 to +6.8 pts/trade, PF 1.23–1.58) — the exit, not the entry, was the biggest lever.

**Changed (evidence ≥ 2 standard errors, or the same direction in both modes)**
| Change | Evidence |
|---|---|
| Paper-trade default exit: **Target 3 at 3 R** (was Target 1 at 1.5 R), SL 1 ATR; diagnostics default exit 60 / 20 | 60-pt target row positive for every stop (best 60/15 +6.8, 60/20 +6.2 pts; the two are not distinguishable, 20 leaves room for slippage) |
| **No entries after 13:30 IST** (was 15:00) | 13:30–15:30 lost in both modes (scalp −4.0 and −9.7 pts/trade, n = 37); mornings made +5 to +12 |
| **New hard gate "Momentum established"**: PE needs RSI < 40, CE needs RSI > 60 | RSI ≥ 40 on a PE: 0 wins of 11 across both modes, −20 pts/trade |
| **Time stop 60 min** (was 30) | the diagnostics horizon is 12 candles; a 60-pt target needs the time |
| **Breakeven trail OFF by default** | never tested; the diagnostics now simulate it (see below) — turn it back on only if the next run says it helps |

**Deliberately NOT changed** (not enough evidence, or probably market direction rather than logic)
* CE vs PE (PE +3.7, CE −1.3 pts/trade) and the BULLISH regime bucket (1 win of 10): this fortnight was a falling market. Blocking CE would be fitting the sample; the new "with / against the session's move" breakdown will show whether it is direction or logic.
* Scalp RSI 18/82 limit (RSI 20–30 entries −1.6 pts/trade, only ~1 SE worse than 30–40).
* Standard-mode "Not chasing" / "Pullback" gates (blocked setups looked better but within noise).
* Score bands (80–89 bad, 90–100 good is not monotonic → noise).

**Diagnostics are harder to fool.** Gate-relaxation advice now needs a two-sample significance test (it previously fired on noise); the best exit is flagged as "best of 36" and shown for the first and second half of the period; its edge vs random entries is measured at that same exit with a stricter 3-SE bar; a breakeven-trail test (+10/15/20/30 pts) is reported; new breakdown "with / against the session's move". Tests: random walks never look profitable at 60/20 once averaged across seeds.

**Before trusting any of this:** re-run `/diagnostics` on a different 15–20-day window (and ideally a rising market). Saved browser settings override the new defaults — reset the Risk guard panel once to pick them up.


## v1.11.2 — second diagnostics run (19 days, 2026-09-25 → 10-23) and honesty checks

**What the re-run showed.** Scalp: 150 signals, +4.2 pts/trade at 60/20 (60/15: +4.9), beats random entries by +7.0 (real), positive in both halves, every cell of the 60-target row positive again, breakeven trail loses again (keep it OFF), 13:30–15:30 entries still lose (cutoff confirmed). Standard: 62 signals, +3.0, not distinguishable from random.

**But:** 14 of the 19 days were already in the first run, so this is not an independent test. The five genuinely new days (Sep 25 – Oct 1) made **−1.25 pts/trade over 34 scalp signals** (standard: −1.3 over 11). And **one day, 2026-10-08, made +473 of the +626 scalp points**; without it the other 132 trades made +1.2/trade. In standard mode, without that day the result is −2.2/trade. Treat this as a trend-day capture system with a ~40 % win rate whose profit arrives on a few days.

**Diagnostics now report** profit concentration by day, a guard-style execution test (one open trade at a time; stop for the day after 2 / 3 losses in a row), and a per-trade list (`trades`, in the downloaded JSON) so any filter can be tested on the exact signals. No further engine rule was changed: every remaining candidate (pause-break trigger −12 pts over 10 trades, BULLISH-bias bucket 1 win in 10, CE vs PE) is either under 2 standard errors, or its trades sit on 2–3 days, or it only mirrors this month's falling market.
