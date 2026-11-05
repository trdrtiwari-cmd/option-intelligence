# Option Intelligence — Upstox Live Derivatives Bridge

## v1.20.12 - 15-minute timeframe

* **Signal freshness by time, not bar count:** the fresh / expire bar counts were tuned on 5-minute candles, so on 15-minute candles a setup stayed "fresh" for 45 min and "live" for 3 hours. Now 5-minute limits are unchanged (standard 15 / 60 min, scalp 10 / 30 min) and slower candles keep the same time limits with a minimum of 2 bars fresh / 4 bars to expire (15-minute: 30 / 60 min).
* **Diagnostics and the strategy monitor replay 15-minute charts with the 30-minute higher timeframe**, exactly like the live analysis (before: no higher timeframe, so the replay did not match live signals).
* Diagnostics "Hold up to (candles)" defaults per timeframe: 1m 48, 3m 20, 5m 12, 15m 8 (about 2 hours).
* To get background alerts / signal logging on 15-minute candles from the open, set `ALERT_INTERVAL=15` (and `ALERT_MODE`) on Render; the app also follows the timeframe you use in the terminal for 6 hours.

## v1.20.11 - Gemini reliability, monitor table layout

* **Gemini:** one call at a time, at least 6 s apart (free-tier rate limit, `GEMINI_MIN_GAP_SEC`); a busy model (503 / 429 / timeout) is retried after 4 s, then the next free model is tried (`gemini-flash-lite-latest` → `gemini-flash-latest` → 2.5 Flash-Lite → 2.5 Flash; override with `GEMINI_MODELS`); a retired model name (404) skips straight to the next; a bad key stops at once. After all of them are busy, calls pause 2 minutes instead of using up the daily allowance. Findings whose AI note failed are retried every 2 minutes (3 at a time). The red error line clears after the next successful call.
* **Monitor table:** left-aligned, readable columns on wide screens; the AI column says why there is no note ("Summarised in the review above" for suggestions, "Explained by the finding itself" for feed / info findings, "Waiting for the AI" while a retry is pending).

## v1.20.10 - Diagnostics: entry window split by time slot

New Diagnostics table "Entry time window by slot": setups blocked ONLY by the entry window (every other gate passed), split into 09:15-09:45, 12:00-14:30 and 15:00-15:20, each with the points exit, the fixed 1.5 / 1.5 ATR exit and the live trailing exit (with first / second half). A slot is "worth opening" only with 15+ setups positive in both halves. Live signals unchanged.

## v1.20.9 - trailing exit plan (now the default)

The 6 Oct - 5 Nov replay (13 signals) made +2.5 pts/trade with the fixed 1.5 / 1.5 ATR exit and +8.7 with a trailing stop on the same 1.5 ATR stop. New exit plan **"trail"** (default; Signals page → Exit plan): stop 1.5 ATR (min 20 pts); once the trade is +1.2 ATR the stop trails 0.75 ATR behind the best price; 3 ATR is only a cap / the R:R basis. Trades record `exit_plan`, `trail_start_pts`, `trail_pts`; exits are settled from the 1-minute path (the trail moves only on completed bars - no look-ahead) and from the live price, with the new status **TRAIL** (stats count it; alerts say "TRAILING STOP hit"). Diagnostics keeps reporting both: the fixed 1.5 / 1.5 plan and the "ATR trail: stop 1.5, trail 0.75 once +1.2" row. Set `EXIT_PLAN=t1.5` to go back. Small sample - paper trade it.

## v1.20.8 - downloads work in the Android app

The app's WebView ignores normal downloads, so Diagnostics "Download report", the Signals Excel / CSV exports, the signal-board CSV, Download for Claude, the account data export and the paper-trade CSV did nothing on the phone. All of them now go through `static/savefile.js`: in the app the file is written to **Documents/OptionIntelligence/** and the share menu opens (Drive, Files, WhatsApp, Claude); in a browser it is a normal download. This needs the new APK (adds the Capacitor Filesystem + Share plugins); an older APK falls back to the share sheet or copying text, and says so.

## v1.20.7 - Diagnostics what-if: trend-day entries

On one-way days the price never pulls back, so the pullback trigger and the 12:00-14:30 gap block every entry (5 Nov: 51 CE setups not bought, 42 of their 1.5 ATR test trades hit target). Diagnostics now replays a stricter trend-day rule **without changing live signals**: same side, score ≥ 70 and every other gate passing except the pullback trigger (variant 2: also not-chasing) and the 12:00-14:30 window; only after 2+ earlier same-side setups of that day had already reached their 1.5 ATR target with no stop since (strictly causal); one trade at a time, max 3 a day; live 1.5 / 1.5 ATR exit. It reports the extra trades alone, by half, and added to the confirmed signals, with a verdict - "promising" only with 15+ trades positive in both halves. The monitor's reviews include it too.

## v1.20.6 - which rule blocked each setup, no duplicate findings

* **Signal board "Blocked by"**: every WATCH / SETUP / NO BUY / FAILED row now stores the exact rules that failed (technical gates, entry window, dashboard, reconfirmation, profit check), shown under "Why". The monitor review gets `today_blocking_rules`: for each rule, how many of today's setups it blocked and how many of those the 1.5 ATR test trade won / lost - the evidence for relaxing or keeping it.
* **Duplicate findings removed**: "not bought but paid" findings were keyed by the board row id (which changes when the board is restored after a Render restart) and the same setup is recorded once per mode / timeframe, so one setup appeared 2-3 times. They are now keyed by symbol + option + minute; existing duplicates are deleted on start-up.
* Download for Claude: compact JSON so the copied text fits a paste.

## v1.20.2 - option last price fixes, monitor times

* **Option price jumping between strikes:** the automatic "2 strikes ITM" pick moves with every 50-pt index move, and the chart followed it, so the last price jumped between contracts with very different premiums. The chart now stays on the contract chosen for a setup; it switches only when the side changes (CE↔PE) or a new setup starts after WAIT.
* **Header price snapping back:** in the option view the 30 s candle reload set the last price to the REST candle close (can lag the live tick by up to a minute). A fresh live tick now always wins.
* **Option chain flicker:** an illiquid strike with no trade for 15 s flipped from the live tick back to the older REST snapshot. The newest price now wins.
* **Monitor:** times shown in IST (were UTC, e.g. 05:31 instead of 11:01); BANKNIFTY findings labelled BANKNIFTY; "not bought but paid" now needs +15 % with the test target (or +30 %), not +4 %.

## v1.20.1 - free ways to get the AI review

* **Download for Claude** (free, uses your Claude chat plan): Signals page → Claude strategy monitor → *Download for Claude*. You get `strategy-review-YYYY-MM-DD.json` with the review instructions and the day's data (trades, losses + causes, missed moves + blocking gates, setups not bought that paid, replay of the last 10 days, code suggestions). Upload it in a Claude chat and write "review this day". Uses the "To date" filter, else today. `GET /api/monitor/export?day=YYYY-MM-DD`.
* **Gemini free tier** as an optional AI for notes and reviews: get a key in Google AI Studio and set `GEMINI_API_KEY` on Render. `MONITOR_PROVIDER` (or the "AI for notes & reviews" selector): `auto` (Claude key if set, else Gemini), `claude`, `gemini`, `off`. `GEMINI_MODEL` defaults to `gemini-flash-lite-latest`. Free-tier requests are rate-limited and Google may use the content to improve its products; a 429 is reported in the monitor status.
* **Off - code only** keeps every check and code suggestion at no cost.

## v1.20.0 - Claude strategy monitor

A new **Claude strategy monitor** panel on the Signals page (and a "Monitor" pill on the terminal). Analysis only: it never places, changes or blocks a trade.

**Found by code (free, always on during market hours):**
* **Feed** (every 5 s): stream down, index ticks > 5 s old, analysis > 10 s old, token expired - "high" when a trade is open (push, max one per 10 min).
* **Rule breaks** (every 60 s): stop under 1.5 ATR, exit after 15:20, stop filled > 0.3 ATR beyond the stop, option premium far from what its delta predicted.
* **Why each loss happened**: gave back a winner (went ≥ 1 ATR your way first), wrong from the first bar, no follow-through, midday entry, ran out of time, option lost more than the index move explains, spike through the stop.
* **Missed moves**: every 30 min the last 10 days are replayed with the live rules; today's ≥ 2.5 ATR moves that were not confirmed are listed with the gates that blocked them. Signal-board setups (WATCH / SETUP / NO BUY / FAILED) that were not bought but then paid are listed too.
* **Drift** (today's win rate 25+ points under the replay) and **suggestions** from the replay: gates whose blocked-only setups made money (candidates to relax) or lost (keep), losing groups, better exits with the same 1.5 ATR stop, the gate behind most missed moves - each with its evidence and a caution.

**Explained by Claude (needs `ANTHROPIC_API_KEY`):** a short note on each loss / missed move / rule break / drift (`MONITOR_MODEL`, default claude-haiku-5-5), an intraday review every `MONITOR_REVIEW_MIN` minutes from 10:00, and an end-of-day review 10 minutes after the close (`REVIEW_MODEL`, default claude-sonnet-5-5) with ranked suggested changes; push "review ready". `MONITOR_MAX_CALLS_PER_DAY` (default 40) caps the API use. Without a key everything above still runs and the review lists the code suggestions. Settings (on/off, Claude, push, calls/day, review interval) are on the Signals page (owner only) and saved in the database; findings and reviews are mirrored to the accounts database like the signal log.

## v1.19.9 - the "Updated" line shows live updates

* While the market is open, the line under Refresh data now shows the time of the last live update to 0.1 s (e.g. "● Live 11:02:15.3 · every 0.1 s · analysis 1.2 s old"). Before, it only changed when the 2 s REST reload or a new analysis arrived, so the 0.1 s feed looked like 2 s.
* After 15:30 it says "Market closed · last session data" and the reload runs every 30 s: no ticks arrive when the exchange is closed.

## v1.19.8 - "Auto refresh" goes down to 0.1 s

* The Auto refresh selector now offers 0.1 s (default), 0.25 s, 0.5 s and 1 s (live), plus 2-60 s. The live choice sets how often the live socket updates this page (price, forming candle, option premium). The full REST reload stays at 2 s minimum and runs only as a fallback. Saved older choices (2 s) are reset to 0.1 s once.

## v1.19.7 - 0.1 s live refresh

* The live socket now checks every **0.1 s** (`WS_TICK_SEC=0.1`, `WS_PUSH_SEC=0.1`): the index price, the forming candle and the selected option's premium move up to 10 times a second (sent only when the price changed); option premiums that moved are pushed as `opt` messages instead of waiting for the 1 s full tick list.
* Candles and analysis are checked every 0.1 s too, but are pushed only when the server holds a newer result. They are rebuilt from Upstox's REST candles (cache 3 s) and the shared analysis (about every 2 s) - polling Upstox 10x a second per page would hit its rate limits and get the token throttled, so those two stay as they are.
* The "Auto refresh" selector (2 s minimum) is now only the fallback when the live socket is down.

## v1.19.6 - real-time push over the live socket, diagnostics corrections

**Why it felt slow:** only option ticks came over the WebSocket (every 0.5 s). The chart re-downloaded **10 days of candles** and the score / decision / buy plan were fetched over REST every 2-4 s, one after the other - on a phone most of that time was spent downloading the same history again.

**Now (`/ws/live`):** the page subscribes once (`{"type":"sub", interval, mode, score_min, reconfirm}`) and the server pushes
* `index` - the index price every time it moves (checked every 0.25 s, `WS_TICK_SEC`);
* `bars` - only the last 3 candles of the chart interval, whenever they change (forming bar incl. futures volume), merged into the chart in place;
* `analysis` - the shared analysis (score, regime, final decision, buy plan, reconfirmation) the moment a newer one is computed (about every second while the market is open, `WS_PUSH_SEC`);
* `ticks` - all option ticks, once a second.
While pushes are arriving the full REST reload runs only every 30 s as a safety net; if the socket drops, the old 2 s polling takes over automatically. The analysis itself still depends on Upstox's REST intraday candles (cached 3 s), so a new 1-minute bar's indicators can be up to ~3 s behind the tick price - the price and forming candle are tick-live.

**Diagnostics corrections (latest NIFTY files, 5 Oct - 4 Nov):** live 1.5/1.5 ATR plan: standard 15 trades 60 % / +6.5 pts, scalp 22 trades 59 % / +7.5 pts; 30/20 points exit 73 % / +13.3 and 68 % / +10.5. The best exit in both files was a trailing stop, so diagnostics now also test **ATR trailing exits that keep your 1.5 ATR minimum stop** (trail 1 ATR once +1 / +1.5 ATR, trail 0.75 ATR once +1.2 ATR, half out at +1.5 ATR) and say whether one beats the live plan. The "versus random entries" line no longer calls a +14-16 pt edge "indistinguishable from random" just because there are fewer than 30 trades (it now says "better than random, not yet proven"), and the ≥ 70 % finder no longer points at the removed "High win-rate" plan - it says when its stop is tighter than your 1.5 ATR minimum. Live entry rules are unchanged: 15-22 trades are not enough to justify re-tuning them.

## v1.19.5 - fixes from the 5 diagnostics files, 1.5 ATR stop, signal board

**What the data showed (5 Oct - 4 Nov 2026, NIFTY 5m, scalp + standard, 30/20 exit, 4 pts cost):**
* **Time of day is the biggest, most consistent factor.** 09:45-12:00 and 14:30-15:00 paid in every file; 12:00-13:30 and 13:30-14:30 lost in every file (13:30-14:30: 31-36 % winners). The latest standard run (21 Oct - 4 Nov) confirms it: the 9 setups blocked ONLY by the old 13:30 cutoff won 78 % (+14.9 pts), while the 12:00-13:30 entries won 33 %.
* With the new entry windows **plus** the v1.19.1 fresh-breakout block, the replayed signals were: scalp 23 trades, 74 % winners, +13.0 pts (first half 88 %, second half 67 %); standard 15 trades, 80 % winners, +16.0 pts (86 % / 75 %). Old rules: scalp 55 % / +3.4, standard 51 % / +1.4. Caveat: the 14:30-15:00 window only had signals in the second half (12 trades); judge it again after 2-3 more weeks.
* Signals vs random entries: still only +2 pts per trade better in the latest file (11 trades) - too few trades to call it an edge yet.

**Changes:**
* **Entry windows 09:45-12:00 and 14:30-15:00 IST** (`ENTRY_WINDOWS`). Diagnostics use the same windows and now end every replayed trade at the 15:20 square-off, as the live app does.
* **Stop-loss at least 1.5 ATR (min 20 pts)** for every BUY, as requested (`MIN_SL_ATR`, `MIN_SL_PTS`). Target by exit plan (Signals page): **1.5 ATR (1 : 1, default)**, 2 ATR or 3 ATR; it is still pulled in before a strong S/R zone, and the BUY is refused when that leaves less than 1 : 1. A custom stop on the Signals page cannot go below 1.5 ATR. Note: in the replays a 1.5 ATR stop with a 2-3 ATR target was among the weakest ATR combinations (prices that went 1.5 ATR against the signal mostly kept going); with a 1.5 ATR target it was among the better ones. Diagnostics' ATR grid now includes 1 ATR targets and 2 ATR stops so you can check your plan cell directly.
* **Signal board** (Signals page): every option signal is recorded automatically - WATCH, SETUP (reconfirming), BUY, NO BUY (with the reason) and FAILED - with the option, its premium and the index at that moment. An hour later the board adds the premium after 30 / 60 min, the best / worst premium in that hour, and the result of a test trade with the plan's stop / target (1.5 / 1.5 ATR when the signal had no plan). Summary per stage, CSV export, and a "Signal Board" sheet in the Excel export. Kept in the accounts database like the signal log.

## v1.19.4 - trendlines drawn correctly

* v1.19.3 could join the morning top to the afternoon highs straight across a 200-point V, and let a support line cut through candles. Trendlines are now **tangent lines**: the support line is an edge of the lower hull of the lows (no low is below it), the resistance line an edge of the upper hull of the highs (no high is above it).
* A line must belong to the current move: price may not have travelled more than 5 ATR away from it, its last touch must be recent, it may not be steeper than 1 ATR per bar, and it must be near price (or price is inside the channel it already swung through).
* On a day like 4 Nov 2026 the support now starts at the afternoon swing low and rises under the rally, and the resistance hugs the rally highs (dashed ✕ once the last candle closes above it).

## v1.19.3 - automatic trendlines on the chart

* New **Auto trendlines** switch (chart tools, on by default, saved with your settings). Works on the index chart and on the option-premium chart.
* **Support trendline** (teal): joins two swing lows; every close since the first point stayed above it. **Resistance trendline** (orange): joins two swing highs; every close stayed below it. When several lines qualify, the one with the most touches wins, then the most recent. Lines that are too steep (a spike) or more than 4 ATR away from price are ignored.
* A line becomes **dashed with ✕** when one of the last three bars closed through it (a break). The label on the price scale shows the line's current value (TL S / TL R).
* The **Trendlines** cell under the chart reads e.g. "Support ↗ 23,948 (3 touches) · Resistance ↘ 24,012", or "BROKEN below" after a break.
* Lines are found from the last ~150 candles (about two sessions on 5-minute), using closed swing points only. They are a chart aid: the signal engine does not use them yet.

## v1.19.2 - no notifications while the market is closed

* **Real trading calendar:** the app reads each day's NSE timings from Upstox (`/v2/market/timings/{date}`, no login needed), so exchange holidays count as closed and special sessions (e.g. Muhurat) as open. Until that answer arrives the Mon-Fri 09:15-15:30 schedule is used.
* **Exit alerts:** a trade that gets settled after the close (for example the server was asleep and works the exit out later from the 1-minute candles) is recorded in the Signals page and Notification history, but no longer sends a phone / Telegram alert. Exits during the session, and the 15:20 square-off (15 min grace), still alert.
* **"Monitor was offline" alert:** only sent when the server wakes up during market hours (it used to fire when the server woke up in the evening).
* **Day summary:** only 5-60 minutes after the close on a trading day, never later at night or on holidays.
* **"Reconnect Upstox" alert:** only from 45 min before the open until the close on trading days.
* Signal alerts re-check the market status at the moment they are sent.

## v1.19.1 - corrections from the 5 Oct - 4 Nov replays (NIFTY 5m, scalp + standard)

Measured on the four diagnostics files (30-pt target / 20-pt stop, 4 pts cost). "First half" = 5-19 Oct, "second half" = 21 Oct - 4 Nov.

| Scalp | trades | winners | avg pts | first half | second half |
|---|---|---|---|---|---|
| Replay as exported | 82 | 55 % | +3.4 | 59 % | 51 % |
| Only the live entry window 09:45-13:30 | 44 | 64 % | +7.8 | 73 % | 55 % |
| + no entry on a fresh breakout / breakdown bar | 26 | **69 %** | **+10.6** | 83 % | 57 % |

Standard mode: 53 → 29 → 19 trades, 51 % → 55 % → 63 % winners (+1.4 → +7.6 pts), but only 40 % in the second half.

* **Fresh breakout / breakdown entries are blocked for both sides, both modes** (`FRESH_BREAK_BLOCK=1`). Scalp BREAKDOWN entries: 32 trades, 44 % winners, -2.1 pts (second half 31 %, -8.6); standard BREAKDOWN 25 %, BREAKOUT 38 %. This replaces v1.19.0's "strong breakout allowed" rule, which rested on a synthetic test; the real data says wait for the retest. `FRESH_BREAK_BLOCK=quality` brings the v1.19.0 rule back.
* **Diagnostics now apply the live entry window** (09:45 - ENTRY_CUTOFF). The old replay also counted 09:15-09:45 and post-13:30 entries the app never sends, which made every report look worse than the live signals (13:30-14:30 entries: 31-36 % winners).
* **Win-rate finder** (Diagnostics): lists exits that reach ≥ 70 % winners with a positive average in BOTH halves of the period - or says that none does.
* **Exit plan setting** (Signals page): *Balanced* (default, 0.75 ATR stop / 3 ATR target, S/R-capped) or *High win-rate* (target 0.6 ATR, min 15 pts / stop 1 ATR, min 20 pts). Small-target exits had 72-83 % winners in the standard replays but only about 0 to +3 pts per trade after costs; switch only if the finder confirms it on your next run.

**Honest status of the 70 % goal:** over the whole month the filtered scalp signals won 69 %, but 57 % in the latest two weeks, on 26 trades. That is not yet proof of a 70 % strategy. Re-run Diagnostics (scalp, 20+ days) after deploying and read the win-rate finder before relying on it.

## v1.19.0 - earlier signals, option buy plan, real P/L per signal, phone layout

**Why 4 Nov 2026 gave one late, losing signal (found in the code, checked on a synthetic range → trend replay):**
* **CE fresh-breakout block (scalp, the background monitor's mode)** - every CE inside a fresh BREAKOUT was blocked, i.e. exactly the bar where a big up-move starts. Once the breakout bar passed, RSI was already above the 68 ceiling, so the next chance was the late pullback. Replay of 30 range → 180-pt trend days: the old rules missed 15 of 30 moves and entered after a median 56 pts; with the fix, 2 missed, entry after a median 27 pts. Now only a WEAK breakout bar is blocked (closes in the lower part of its range, blow-off bar > 2 ATR, or already > 1 ATR beyond the old range). `CE_BLOCK_BREAKOUT=strict` restores the old rule.
* **"Score < 90" gate** - blocked every reading of 90+ (the screenshot: UPTREND, score 92, WAIT). Off by default; `SCORE_CAP=90` restores it. The duplicate "before 13:30" gate is gone (the entry window `ENTRY_CUTOFF` already does that).
* **ADX (14) and Supertrend (10,3) as hard gates in standard mode** - the two slowest inputs; they now pass on a proven price-structure trend, as scalp already did (`LAG_RELIEF=0` restores). They still count in the score.
* **RSI 68 / 32 ceiling kept** - it is the most frequent blocker in a strong trend, but your v1.16 real-data replays show entries beyond it lose money, so it was not loosened.
* **The -74 pt "stop"**: the exit watcher filled the stop at the first price it saw after a pause (server asleep / feed stall). Exits now come from the **1-minute index path** since entry: the first bar that touched the stop or target decides, filled at that level (or the bar's open on a gap).
* **Check it on your own data:** Diagnostics now has **Missed big moves** - every clean ≥ 2.5 ATR move the engine did not confirm in time, and which gate was failing when it started.

**Option flow:** WATCH / reconfirming / confirmed CE or PE → the option **2 strikes in the money** on that side (CE 2 strikes below ATM, PE 2 strikes above; offset and ITM/OTM on the Signals page) is chosen, put on the live feed, and the terminal chart switches to it (Signal option: Auto-switch). On confirmation the **profit check** runs: target = 3 ATR but brought in just before the next Medium/Strong S/R zone, index reward:risk ≥ 1.5, premium levels from chain delta/gamma/theta, profit at target ≥ 2 × charges, premium R:R ≥ 1.2, spread ≤ 3 %, premium not trending down. Only then is it a **BUY**, logged and pushed.

**Daily Signal Log:** each BUY records option, buy price, stop-loss, target (premium and index), target basis, R:R, exit premium (live quote or the option's own 1-minute bar), net ₹ after charges and net index points. Day / month results show net ₹.

**Notifications:** WATCH (with the option the chart switched to), setup detected (with the option lined up), **BUY** (option + index levels + ₹ at target/stop), **NO BUY** when a confirmed setup fails the profit check (All-changes only), and **EXIT** (target / stop / time, premium and ₹ result).

**Phone layout:** tables no longer break words letter by letter. The S/R map, weighted checks, candle patterns, paper trades and the signal log/results are cards (label : value); wide data tables scroll sideways with whole numbers; long card notes are clamped (tap to expand); the header buttons wrap.

New environment settings (all optional): `OPTION_STRIKE_STEPS` (2), `OPTION_MONEYNESS` (ITM), `OPTION_LOTS` (1), `OPTION_CHARGES_RS` (60), `OPTION_MIN_RR` (1.5), `OPTION_MIN_TARGET_ATR` (1.2), `OPTION_SR_BUFFER_ATR` (0.15), `OPTION_MAX_SPREAD_PCT` (3), `OPTION_MIN_PROFIT_X_CHARGES` (2), `OPTION_MIN_PREMIUM_RR` (1.2), `OPTION_MAX_PREMIUM_SL_PCT` (25), `OPTION_HOLD_MINUTES` (45), `SCORE_CAP` (0 = off), `LAG_RELIEF` (1), `CE_BLOCK_BREAKOUT` (1 / strict / 0).

## v1.18.14 - push alerts as close to "always on" as browsers allow

* The Android app asks for notification permission at launch (no tap needed). In the browser/PWA the first tap on each page load asks (up to 3 times), and an orange bar at the top offers a one-tap **Turn on** until push is on; it also explains how to unblock notifications or add the app to the iPhone Home Screen.

## v1.18.13 - choose which signals send a notification

* New **Notifications** setting on the phone (More menu) and next to *Enable push alerts* on the terminal: **All signal changes** or **Confirmed buy signals only**. It is saved on your account and the server applies it per account, so it works with the app closed.
* *All signal changes* = setup detected, CONFIRMED, failed reconfirmation, and any other change of the final signal (e.g. WATCH CE to CONFLICT). *Confirmed only* = just CONFIRMED buy CE/PE. Day summary, token-expired, monitor-offline and test alerts always arrive. Default is *All* (same alerts as before, plus changes).
* Telegram / ntfy / email still follow `ALERT_EVENTS`.

## v1.18.12 - push stays on, owner check fixed, signal log follows the terminal

* **Push alerts no longer need enabling again and again.** The phone now shows its last known state instantly, silently re-registers with the server on every open / return to the app / every 10 min whenever notification permission is granted, and asks for permission once on the first tap (at most once a day; never when blocked). A slow service worker is no longer read as "push off".
* A 400 reply from the push service no longer deletes a phone's registration (only 401/403/404/410 do).
* **"No web device is registered for the configured owner account"** now counts Android-app (native) devices too, and tells you which user id(s) actually have devices. Either set `PUSH_OWNER_USER_ID` to that id or leave it empty to notify every account.
* **Signal log:** the background monitor now also analyses the settings (interval / mode / score) your terminal used in the last 6 hours (max 3 extra; `ALERT_FOLLOW_TERMINAL=0` turns it off), so confirmed signals are logged even when the page is closed. `/api/push/status` shows the last logged signal and any write error.

## v1.18.11 - alerts that actually arrive + faster data

Why alerts did not arrive / "push turned itself off" (found by analysing the app, not guessed):
* **Push was sent with TTL 0** (the library default): if the phone was asleep at that instant the push service threw the message away. Now TTL 10 min (`PUSH_TTL_SECONDS`) with high urgency (and high priority for Android FCM).
* **Rejected registrations were never removed**: when the push service answered 403/400/401 (key changed) the registration stayed forever and every alert silently failed while the button said ON. They are now dropped and the phone re-registers itself the next time the app opens.
* **The button label depended on a slow server call**, so after reopening it showed "Enable push alerts" until the server answered. It now reads the real state from the phone itself (permission + subscription) instantly.
* **The server sleeps when nobody opens the app** (Render Free). A monitor that is asleep cannot send anything. The app now saves a heartbeat and, on the next start, tells you "monitor was offline 10:05-10:47, signals in that window were not watched". Keep it awake with a pinger on `/health` every 5 min: UptimeRobot / cron-job.org, or the included GitHub Action `.github/workflows/keepalive.yml` (add the `APP_URL` secret).
* Default alert events are now `STARTED,CONFIRMED` (setup detected + confirmed). Set `ALERT_EVENTS=CONFIRMED` for confirmed only. History records every alert, including system notices.
* **See what is wrong**: the terminal shows a monitor line under the alert buttons, and **Notification history** now opens an "Alert monitor" panel (server up, market, Upstox token, monitor running, per-symbol state, phones registered, last alert, and a plain list of problems). "Test alert" now says exactly what happened per channel (e.g. "phone push: NO DEVICE REGISTERED").

Speed / stability:
* One shared keep-alive connection to Upstox (was a new TLS handshake per call), identical simultaneous calls share one upstream request, 429/gateway errors are retried once or twice, concurrency capped (`UPSTOX_MAX_CONCURRENT`, default 8). `/api/status` no longer waits on Upstox. gzip level 3. The warmer only fills gaps (not duplicating a polling page) and backs off after errors.
* Saved-data badge moved to the bottom (it covered the title) and the request behind it can no longer hang forever.

## v1.18.10 - self-updating page

* The "page is out of date, press Ctrl+F5" banner could not be acted on from a phone. The app now clears its own saved files and reloads automatically once when the server has a newer version, and offers an **Update now** button if that was not enough. (From an older version: More > Reload app does the same.)

## v1.18.9 - cards follow the chart view

* In **option view** the Last price, Score, Trend and Decision cards now describe the selected CE/PE's own premium chart (`/api/option-analysis`), and the Trend card shows both: `Chart (CE 24500 premium): UPTREND · Index: UPTREND`.
* Decision combines both: **BUY CE/PE** only when the index signal is CONFIRMED on that side AND the premium trend is up; otherwise WAIT (premium not confirming), AVOID (index signal is the other side), WATCH, or NO BUY (premium falling).
* In index view the cards describe the index, labelled "Chart view: index". Alerts and the logged signals still use the index confirmation engine.

## v1.18.8 - instant open + no more frozen refresh

* **Fixed: data stuck at an old time after reopening the app.** If the phone froze the page mid-request, that request never finished and the refresh loop waited for it forever. Every request now has a 20 s deadline, every "busy" flag has a watchdog, and coming back to the app (unlock, switch back, network returns) discards anything stuck and reloads everything at once. Same fix on the Data Check page.
* **Instant open**: the last live data (candles, analysis, derivatives, Data Check, signals, option chain) is kept on the device and shown immediately on the next open, with a small "Saved data from N min ago - updating" badge until the live answer lands. The page no longer waits for the account sync before starting, and the three main pages open from the installed app's saved copy even while the server is waking up.
* **Server**: one analysis is shared by every phone/tab/the alert poller (2 s fresh, up to 15 s served instantly while refreshing; 15 min when the market is closed), a background warmer keeps whatever you looked at recently computed, and responses are gzip-compressed. `WARMER=0` turns the warmer off.
* A web page cannot run while the phone is locked - background alerts come from the server push (v1.18.6). Opening the app now simply catches up instantly.

## v1.18.7 - saved daily & monthly strategy results

* Every confirmed BUY CE/PE is logged automatically at the signal-time index price with an ATR stop-loss and target, then auto-exited (target, stop or 15:20 square-off). Each row now also stores the ATR and the stop/target multiples used.
* Signals page > **Strategy results**: net P/L by day or by month (win rate, profit factor, expectancy, max drawdown, ₹ estimate). Excel export has Daily and Monthly sheets.
* **Flexible ATR**: owner sets stop and target as multiples of ATR on the Signals page; applies to new signals only.
* The log is mirrored into the accounts database and restored after a restart (set DATABASE_URL to PostgreSQL on Render).
* A day-summary push is sent after 15:35 IST on trading days.

## v1.18.6 - phone fit + automatic background alerts

* **Phone layout**: nothing is wider than the screen any more (320-430 px). Long values wrap, grids shrink to one/two columns, and wide tables scroll inside their own box with the first column pinned.
* **Web Push needs no setup**: the server generates its own Web Push (VAPID) key pair on first start and keeps it in the accounts database. `VAPID_PUBLIC_KEY` / `VAPID_PRIVATE_KEY` are now optional overrides. The poller therefore starts by itself.
* **How alerts work with the app closed**: a background poller analyses NIFTY/BANKNIFTY every 10 s during market hours. When a BUY CE/PE passes the reconfirmation hold, it sends a push to every registered phone (and Telegram/ntfy/email if configured) and stores it in Notification history.
* **One-time step per phone**: open the app, tap **Enable push alerts**, allow notifications. After that the app re-registers silently on every visit. On iPhone: Safari > Share > Add to Home Screen, open from the icon, then tap Enable.
* **Upstox token**: expires daily; when it does, you now get a push telling you to reconnect (once a day).
* **Staying awake on Render Free**: the app pings its own URL every 10 min (`KEEPALIVE=0` to disable), but a service that has already gone to sleep must be woken from outside. Add a free monitor (UptimeRobot / cron-job.org) hitting `https://YOUR-APP.onrender.com/health` every 5 min. A paid instance needs none of this.
* Set `DATABASE_URL` (PostgreSQL) so phone registrations and the generated key survive redeploys; otherwise phones must tap Enable again after each redeploy.

## v1.18.5 — push database fix
- **Root cause:** push device registrations and notification history were kept in a local SQLite file (`push_notifications.sqlite3`), which Render Free erases on every restart/deploy, so phones silently stopped receiving alerts until they re-registered.
- Both tables now live in the accounts database (PostgreSQL when `DATABASE_URL` is set). `PUSH_DB` is no longer used and no persistent disk is needed.
- `PUSH_OWNER_USER_ID` is now optional: when empty, alerts go to every account that registered a device.
- Database and FCM/Web Push calls run in worker threads (they previously blocked the event loop); FCM tokens that report "unregistered" are pruned correctly; a database outage returns a clear 503 instead of a crash.
- Devices registered before this version must open the app and enable notifications once more.

## v1.18.0 — finalized background and push alert routing
- Confirmed-signal push can run with Firebase FCM or Web Push alone; Telegram/ntfy/email are optional additional channels.
- Background polling is enabled whenever any server-side notification transport is configured.
- `/api/alerts/test` dispatches a test to configured server channels and registered push devices.
- `/api/auth/me` returns `user.id`; set that numeric value as `PUSH_OWNER_USER_ID` for background push routing and notification history.
- Device registrations and notification history are stored in SQLite. Use a persistent disk on the hosting provider and set `PUSH_DB` to its mounted path; otherwise registrations/history can disappear on redeploy/restart.

See `mobile/README.md` and `.env.example` for setup. Background delivery still requires the backend to remain awake; Render Free sleeps and is not suitable for dependable unattended monitoring.

## v1.17.5 — a pending local change (e.g. an open paper trade) no longer blocks pulling changes from other devices; More -> Sync now shows the signed-in account, last check and any sync problem.

## v1.17.4 — the "Waiting for data" label is replaced by the update time in the option-premium chart view.

## v1.17.3 — chart view "Selected option premium" and its option contract now follow you across devices live.

## v1.17.2 — settings changed on another device now reach an open or backgrounded app as soon as you return to it (and every 30 s), instead of only on a fresh start; risk-guard and capital changes reload the page.

## v1.17.1 — fix: the "Selected option premium" chart view now survives a reload (the selected option contract is saved too).

## v1.17.0 — mobile app + every setting saved to your account
- **Mobile app (installable):** the site is now a PWA - open it on a phone and use *More -> Install app* (Android) or Share -> *Add to Home Screen* (iPhone). Phone layout with a bottom tab bar (Terminal / Data / Signals / Tests / More), larger touch targets, collapsible signal controls, offline screen, "keep screen awake" switch, safe-area support. Static files are cached; prices, signals and accounts are never cached.
- **Native Android/iOS wrapper:** `mobile/` (Capacitor) loads your hosted site, so every deploy updates the app. A GitHub Action (`.github/workflows/android-apk.yml`) builds an installable APK for you - see `mobile/README.md`.
- **Automatic settings save:** symbol, timeframe, chart overlays, S/R timeframe, option filters, paper-trade risk-form choices, Data Check and Diagnostics controls, signal-log filters, minimum score, reconfirm hold, alert sound, keep-awake and more are saved to your account a moment after you change them and restored on every device. Per-field merge: the newest change to each setting wins, so two devices changing different settings never overwrite each other. A change made on another device is applied within about a minute. *Account -> Saved settings* lists them and can reset them.
- The Signal Log and Account pages now use account sync too (they previously kept their inputs only in the browser).
- New files: `static/prefs.js`, `static/mobile.js`, `static/mobile.css`, `static/sw.js`, `static/manifest.webmanifest`, `static/offline.html`, `static/icons/`. Tests: `python tests/test_mobile.py`, `node tests/sync/run.js`.
- Not included: push notifications while the app is closed (use the existing Telegram / ntfy / email alerts), and Play Store / App Store publishing.

## v1.14.0 — signal-log auto-exit, net P/L, live-price label fix
- **Why the log never closed a trade:** the logger read `entry` / `stop_loss` / `target` from the ATR plan, but the plan returns `entry_index` / `sl_index` / `targets[]`, so every row was saved with a blank stop and target. Exits were also only checked when a *new* confirmed signal appeared. Both fixed.
- **Auto-exit** (`signal_exits.py`): every logged trade closes at the first of STOP (filled at the observed price), TARGET (filled at the target level), or SQUAREOFF at 15:20 IST. Stop wins if both are crossed in one observation. Trades from an earlier session that were never settled become EXPIRED with no invented P/L. A background watcher checks the live index price every 3 s while the market is open; every analysis refresh and the **Check exits now** button also settle trades.
- **Net P/L** = gross index points − `SIGNAL_LOG_COST_POINTS` (default 4). The page shows win rate, exit counts, closed net P/L, open (mark-to-market) P/L and an optional rupee estimate (points × option delta × quantity). Excel/CSV exports include exit time, reason, peak/worst points; the Daily Summary sheet has per-day P/L.
- **Safe target** is `SIGNAL_LOG_TARGET` = `T2` (3 ATR, default), `T1` (1.5 ATR) or `T3` (4.5 ATR). T1 is not recommended: in the 21-day replays a 1.5 ATR target with a 1 ATR stop averaged -0.9 pts/trade (scalp) and +0.7 (standard) after cost, against +5.1 / +6.4 for 3 ATR. Time exit: `SIGNAL_LOG_SQUAREOFF` (default `15:20`).
- Old rows saved without levels are given a stop/target from the current ATR the next time analysis runs (marked `*`). Their entry is the logged index price.
- **Price label:** "REST quote (WebSocket idle)" was shown whenever no tick had arrived, including after the market closes when no ticks exist. It now reads "Last close · market closed" after hours, and during market hours "REST quote · waiting for live tick" or "REST quote · reconnecting live feed" (and the browser socket is reconnected if it dropped).
- Tests: `python tests/test_signal_exits.py`.

## Threshold comparison (v1.13.4)
- Diagnostics supports minimum score 70, 65, 60, and 0 (research-only) with all non-score safety gates retained.
- Compare thresholds on the same symbol, dates, timeframe, costs, target, stop and horizon. Treat threshold 0 as research only; it does not mean a live trade can bypass safety gates.
- The live terminal still defaults to score 70 unless the user selects a test threshold.


## Experimental score threshold (v1.13.2)
- Main terminal has a configurable minimum directional score: 70 (strict, default), 65 (test), or 60 (relaxed test).
- The chosen threshold is passed to the analysis endpoint and saved in browser local storage. Other safety gates remain unchanged.
- This is an experiment control, not evidence that a lower threshold is more profitable. Compare against score ≥70 on identical out-of-sample dates and costs before changing the live default.

## Reliability fixes (v1.11.3)
- Fixed the main terminal startup race where `refresh()` ran before `refreshBusy` was initialized, preventing the first automatic load.
- Returning to the main terminal now triggers an immediate refresh even if the market-status poll has not updated yet; automatic polling still respects market-open status.
- Closed paper-trade history deletion now syncs across signed-in devices with trade-ID tombstones, preventing server-side merge from restoring cleared closed trades. Open paper trades are retained.
- Kept signal rules unchanged pending stronger out-of-sample evidence; see diagnostics limitations before relying on exit-grid winners.


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

### v1.11.1 — corrections from the first 14-day replay (NIFTY 5m, 2026-10-05 → 10-23, cost 4 pts)
What the data showed, and what changed:
* **Late entries lose.** Entries before 12:00 made money (scalp: +7.7 pts/trade, 52 trades); entries after 13:30 lost (−5.4, 37 trades) in both modes. → default entry cut-off moved from 15:00 to **13:30** (`ENTRY_CUTOFF=HH:MM` in the environment overrides it).
* **Exhausted-RSI entries lose** (standard: setups blocked only by RSI < 30 / > 70 averaged −14.7; scalp bucket RSI 20–30: −1.6 vs +4.1 for 30–40). → scalp mode no longer relaxes the RSI gate (30 / 70 in both modes).
* **Small fixed targets lose, wide ones win.** After cost, targets ≤ 25 pts were negative for every stop; target 60 / stop 15–20 was +5 to +7 pts/trade. Median favourable move was 37 pts but the median adverse move 26 pts, so a 20-pt stop is hit 46% of the time. → paper trading now defaults to **Auto-exit at Target 3** (3.5 × risk) instead of Target 1 (1.5 ×). Diagnostics now also compares trailing and scale-out exits.
* **Kept as is:** the relaxed chase limit (entries 1–2 ATR beyond EMA 9 were the best bucket; the strict gate blocked setups that averaged +5.6), the "not sideways" and trigger gates (the setups they blocked lost money). The new pause-break trigger fired only 3 times in 14 days; EMA 9 retests give nearly all entries.
* **Not changed (not enough evidence):** CE signals lost money (−1.3/trade) while PE made +3.7, and the generic "BULLISH" regime label lost 9 of 10 — but this was a falling market. Diagnostics now includes the dashboard's price-zone check and previous-close side so the next run can test whether requiring them fixes it.

## v1.11.2 — real-time option feed, ATR levels on the Data Check dashboard, corrections from the 10-session replay

**Why the selected option was "very delayed".** (1) The contract you clicked was never added to the WebSocket unless it happened to be one of the 12 nearest strikes, so its price came from REST. (2) In *Selected option premium* view the page only re-downloaded REST candles; Upstox's intraday-candle endpoint itself lags, and nothing moved the last candle between refreshes (the index had a live-tick path, options did not). (3) The Data Check dashboard built its ATM legs and premium graph from the REST chain / REST candles only.

**Fixes.** `GET /api/pin-option` (called on selection) and a `PINNED_KEYS` set keep the selected contract, the dashboard's ATM ±2 strikes and its graph legs subscribed (they survive the near-ATM re-subscription). Every WebSocket tick of the selected option now moves the last premium candle in the browser (`● LIVE option tick`). `/api/candles` for an option and the dashboard premium graph fold the newest fresh tick into the REST candles (`overlay_live_tick`), so a REST refresh can never show older data than the stream. The dashboard's ATM strike uses the live index tick instead of the 4 s old chain spot, and breadth/sector quotes are cached 2 s (was 5 s). Breadth and sectors are still REST (~2 s) — they are 60+ instruments and I could not test a bulk "ltpc" subscription against your Upstox plan limits.

**ATR stop-loss and targets on the dashboard.** The final-signal panel now has an *ATR stop-loss & targets* block: Wilder ATR(14) of closed 5-minute index candles; stop = 1.25 ATR since v1.15 (was 1.0; never < 10 pts); T1 = 1.5 ATR (sell half, stop to cost), T2 = 3 ATR (main), T3 = 4.5 ATR (runner, trail 1 ATR). It also shows approximate option-premium levels (delta ≈ 0.5, stop capped at 25 % of premium). Settings: `THRESHOLDS["atr_*"]` in `dashboard_data.py`. `/api/analysis` returns `atr_levels` too. Diagnostics now runs an **ATR exit grid** (target 1.5–5 ATR × stop 0.5–1.5 ATR) and prints the best one with the median ATR — re-run it and set the multiples from that.

**Corrections from your two diagnostics files (NIFTY 5m, 2026-10-12 → 10-26).**
| Finding | Evidence | Change |
|---|---|---|
| Exit matters more than entry | 30/20 = −0.9 (standard) / +1.8 (scalp); 60/15–20 = +6.3 / +10.1 | Targets are 3 ATR main / 1 ATR stop, not 30/20 |
| Weak-momentum entries lose | RSI "extremity" ≥ 40: 5 of 5 lost, both modes | New gate: RSI ≤ 40 for PE / ≥ 60 for CE (`RSI_MOMENTUM` env, 0 disables) |
| CE on lagging indicators lost | generic-BULLISH CE: 2 wins of 9 (−11.2/trade); all other CE are structure-confirmed | New gate: CE needs a price-structure uptrend (`CE_NEEDS_STRUCTURE=0` disables) |
| Signal vs random | edge +2.6 / +5.3 pts, inside noise | Not changed — keep paper trading |
| Time of day | 13:30+ was positive here, negative in the earlier run | Cut-off left at 13:30 — contradictory, re-test |
| Score 90–100 lost in scalp (n=7), stretch < 0.5 ATR lost (n=18) | small samples | Not changed — watch in the next run |

Applying only the RSI gate to the replayed trades would have turned −0.9 → +2.9 (standard, n=26) and +1.8 → +4.3 (scalp, n=46) per trade at 30/20; adding the CE rule gives +6.0 in both. These were chosen **after** seeing the same 31/51 trades, so they are optimistic — confirm on a different period before trusting them.


## v1.14 - side-specific RSI, reconfirmation window, alerts

**RSI per side.** CE and PE now have their own RSI limits instead of one shared rule. Defaults keep the replay-tested values:
CE needs RSI 60-68 (momentum floor 60, exhaustion ceiling 68 since v1.16), PE needs RSI 32-40 (exhaustion floor 32 since v1.16). In scalp mode, once the price-structure trend is
confirmed, only the momentum edge relaxes (CE >= 58, PE <= 42); the exhaustion edge stays strict. Each edge can be changed from the
environment (`RSI_CE_MIN`, `RSI_CE_MAX`, `RSI_PE_MIN`, `RSI_PE_MAX`, `*_SCALP`). A new gate also asks that RSI is moving with the trade
on the last closed bar (rising for CE, falling for PE); `RSI_SLOPE=0` removes it.

**Reconfirmation.** A setup that passes every gate is shown as `RECONFIRMING CE/PE - Ns left` and is not logged or alerted as an entry.
It becomes CONFIRMED only after it has kept passing every gate for the chosen hold time (page selector "Reconfirm hold", default 60 s,
server default `RECONFIRM_SECONDS`) across at least two looks, and price has not moved more than `RECONFIRM_MAX_ADVERSE_ATR` (0.5 ATR)
against the price at which it was first seen. A short flicker (20 s) does not reset the clock; if price moves against it the attempt
FAILS and stays failed until the setup disappears and forms again. The clock needs the server to keep looking: an open page does it, and
the background alert poller does it when no tab is open.

**Alerts.** In the page: a beep (CE rises, PE falls, played twice when confirmed; a double tick when a setup is first detected; a low buzz
when a reconfirmation fails), a browser notification and a flashing tab title. Browsers only allow sound after you click something on the
page once; press **Test alert**. On the server: Telegram, ntfy, or email through the Resend HTTPS API (see `.env.example`). Plain SMTP is
included but Render's free plan blocks SMTP ports (25/465/587), so it only works locally or on a paid instance. Server alerts do not know
the page-only S/R-room and option-volume gates, so check the page before entering.

## v1.15 - corrections from the Oct 15-30 diagnostics (scalp 61 signals, standard 27)

What the replay said, and what changed. All of it rests on 11 trading days, so treat it as a hypothesis to confirm on paper trades.

| Finding | Change |
|---|---|
| Exit matters more than entry: 30/20 fixed = +1.0 (scalp) / +1.9 (standard) pts per trade, 3 ATR target / 1.5 ATR stop = +4.5 / +4.1; the edge disappears at 6 pts cost with a 30-pt target | Main target stays 3 ATR (T2); stop 1.0 -> **1.25 ATR** (the earlier 21-day replay favoured 1.0, this one favours 1.5; 1.25 splits the difference). Management notes now say: hold to T2, no early break-even or trailing (all such variants averaged -0.5 to +1.3) |
| One day (Oct 27: 9 signals, 1 winner, -166 pts) cost 2.6x the whole period's profit; entries right after a loss won 44% vs 52% after a win | New gate **daily loss limit**: no new confirmed entries after 2 stop-outs in a day (`MAX_DAILY_LOSSES`, 0 = off). Scalp 11-day total +63 -> +140 pts; standard unchanged (+52 -> +50) |
| Every 09:15-09:45 entry lost (4 of 4), 09:45-10:30 averaged +9 pts | Entry window now opens at **09:45** (was 09:30) |
| CE entries inside a fresh BREAKOUT phase: 12 trades, 33% wins, -88 pts; CE in an established TREND +62 | New scalp gate **CE not inside a fresh breakout** (`CE_BLOCK_BREAKOUT=0` removes it). Lowest-confidence change |
| Score 90-100: 22% wins, -12.9 pts per trade (already blocked by the live score < 90 gate) | Unchanged; the diagnostics replay simply does not apply live-only gates (time window, score cap, dashboard) |

Not changed on purpose: the "Not sideways" gate (the 10 setups it alone blocked averaged +11 pts, but n = 10, SE 7.6) and the 13:30 cutoff (scalp 13:30-14:30 lost 109 pts but 14:30-15:30 won 82; standard disagreed). Re-run the diagnostics with target 60 / stop 20 to see the random-entry baseline for that exit before trusting any fixed-point exit.


## v1.16 - corrections from the Oct 5-30 diagnostics (scalp 81 signals, standard 52; plus 9-day re-runs)

These files were produced by v1.15, so they measure the current engine. Method: every finding had to point the same way in both halves of the window (Oct 5-16 and Oct 19-30) and in both modes before it changed a number; findings that flipped between halves were left alone.

| Finding | Evidence | Change |
|---|---|---|
| **Largest loss source: entries with RSI already stretched.** PE with RSI 30-32 / CE with RSI 68-70 (within 2 points of the old exhaustion edge) | 28 of 81 scalp signals, 39% winners, -133 pts; the other 53 made +428 (+8.1 avg). Raising the floor helped scalp (+295 to +436 at floor 32), standard (+160 to +264) and both older 11-day replays; the gain peaks at 32-33 in all four | PE RSI floor 30 -> **32**, CE ceiling 70 -> **68** (`RSI_PE_MIN`, `RSI_CE_MAX`) |
| **Stop too wide for the current engine.** v1.15 set 1.25 ATR from the older replay; the new replays reverse it | ATR grid at a 3 ATR target, averaged over six replays: stop 0.5 ATR +4.0, **0.75 ATR +4.4**, 1.0 +2.7, 1.5 +2.8. A 20-pt stop with a 60-pt target is the best fixed cell in all six (+3.4 to +9.8 pts per trade); a 10-pt stop is clearly worse | Stop **0.75 ATR**, minimum **15 pts** (was 1.25 ATR / 10); main target stays 3 ATR |
| Early break-even / trailing / scale-out | Every variant lost to holding to the target in all four new replays (scalp: +0.7 to +5.5 vs +9.7 for a fixed 60-pt target) | Management notes say hold to T2 (unchanged from v1.15) |
| Daily loss limit | Before the RSI fix it lifted scalp from +295 to +427; after the fix it rarely triggers (worst day -22 to -24 pts) | Kept at 2 stop-outs as a safety net |
| 13:30 cutoff and 09:45 start | After the RSI fix, a 14:30 cutoff adds +40 for scalp but loses 38 for standard; a 09:45 start beats 09:15/09:30 (+486 vs +436 scalp) | Unchanged |

Checked and deliberately **not** changed: "Not sideways" gate (the 18 / 16 setups it alone blocked averaged -1.8 / -2.1 pts, so the gate is right; the earlier "relax it" hint was noise), "CE needs structure" and "CE not inside a fresh breakout" (blocked 21 and 32 setups averaging -7.9 and -1.7), scalp chase limit (a 1.5 ATR cap looked good alone but cost -108 pts once the RSI fix was in), score gate (the 10 setups blocked only by score < 70 averaged +11, n = 10, SE 7.6; the page's Minimum score 65 option tests this), ATR-size buckets and the 12:00-13:00 window (non-monotone or opposite signs in the two halves).


## v1.17.6 — background monitoring and confirmed-signal alerts

- Background alert polling now watches **NIFTY and BANKNIFTY** by default.
- Phone alerts default to **CONFIRMED only** (not preliminary STARTED setups). Override with `ALERT_EVENTS` if desired.
- The server-side poller continues to run with the browser/mobile app closed, but only while the hosted backend process is awake and has a valid Upstox access token.
- For phone notifications, configure **Telegram** (`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`) or **ntfy** (`NTFY_TOPIC`) in Render Environment. Then redeploy and use Terminal → **Test alert** to verify delivery.
- Set `ALERT_BACKGROUND=1`, `ALERT_SYMBOLS=NIFTY,BANKNIFTY`, `ALERT_INTERVAL=5`, `ALERT_MODE=scalp`, `ALERT_SCORE_MIN=70`, `ALERT_EVENTS=CONFIRMED`.
- **Important hosting limitation:** Render's free web service may sleep when idle. No website, PWA, or WebView can reliably execute its JavaScript while the phone OS suspends it, and the server cannot poll while Render is asleep. Reliable closed-app alerts therefore require an always-on backend/worker plus Telegram or ntfy push. Android battery optimization should also be disabled for the ntfy/Telegram app if delivery is delayed.
- This is alerting/analysis only; it does not place orders. Confirm the live data and risk levels before trading.


### v1.18.0 deployment hotfix
- Fixed `BASE_DIR` initialization order so `PUSH_DB` can be configured without a startup `NameError`.
- Verified module import, Python compilation, frontend/service-worker JavaScript syntax, and all 89 existing tests.
