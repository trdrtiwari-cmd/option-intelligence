# Option Intelligence — Upstox Live Derivatives Bridge

A FastAPI website for NIFTY 50 / BANK NIFTY research and paper-trading. It uses Upstox REST APIs for option-chain snapshots and the official Upstox Python SDK's Market Data Feed V3 WebSocket for subscribed near-ATM option contracts.

## What this build includes

- NIFTY 50 / BANK NIFTY historical candle chart and basic technical analysis.
- Current-week option-chain lookup; automatically falls back to current-month chain if the weekly chain returns no rows.
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
   - `TRADINGVIEW_WEBHOOK_SECRET` (optional, long random value; enables protected webhook endpoint)
6. Register the exact same redirect URI in the Upstox developer app, redeploy, then use **Connect Upstox**.
7. Check `/api/status`, then load live derivatives during market hours.

### Token note

OAuth callback stores the access token in process memory for this starter build. A restart/redeploy loses it. For production, use a secure durable token/session mechanism consistent with Upstox's current authentication policy. Do not store secrets in frontend JavaScript or public GitHub files.

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

This is one-way only. TradingView can send a strategy alert to the website; the website cannot feed its REST values back into Pine.

1. Set `TRADINGVIEW_WEBHOOK_SECRET` in Render to a long random secret.
2. In TradingView, create an alert on the Pine strategy and use the webhook URL `https://YOUR-SERVICE.onrender.com/api/tradingview/webhook`.
3. In the alert message, use JSON like the following, replacing the secret locally (never share it in chat):

```json
{
  "secret": "YOUR_LONG_RANDOM_WEBHOOK_SECRET",
  "source": "TradingView",
  "ticker": "{{ticker}}",
  "interval": "{{interval}}",
  "close": "{{close}}",
  "alert": "BUY CE confirmation"
}
```

For a BUY PE alert, change the `alert` value accordingly and create a separate alert if needed. The endpoint stores the latest alert in process memory and does not execute orders. Do not put your Upstox API key, API secret or access token in the TradingView alert message.


## Paper Trading Dashboard (browser-local)
- Select a CE/PE contract using the `Use CE` / `Use PE` buttons in the option-chain table.
- ATR mode calculates ATR from the selected option contract's premium candles. It does not use the index ATR.
- Manual mode allows user-entered stop-loss and targets.
- Paper trades are stored in this browser's localStorage. They are not synced across devices or users and are not real broker orders.
- The dashboard periodically requests the selected option's quote. WebSocket ticks are preferred when fresh; otherwise the quote endpoint labels an Upstox REST snapshot separately.
- Stop-loss/target exits are simulations based on observed quotes, not guaranteed fill prices. Gaps, spread, latency, fees, taxes and slippage can make real outcomes differ.
- Quantity is in option units, not lots. Enter the desired unit quantity; the app does not assume a lot size.
- Public visitors have separate browser-local paper-trade histories. Do not expose API credentials in frontend code or public repositories.


## Chart overlays and selected-option premium chart
- The chart now includes toggleable EMA 9/21, approximate volume-weighted VWAP, and automatically detected recent swing support/resistance levels.
- The RSI(14), ATR(14), relative volume, support and resistance values are shown beneath the chart. The Market Intelligence panel follows the active chart source.
- To view an option premium chart, select a CE/PE using the option-chain table, then choose **Selected option premium** in Chart view. The app requests historical candles using that selected contract's Upstox instrument key. If Upstox returns no historical candles for the contract, the chart displays an unavailable message instead of substituting index candles.
- These chart candles are historical REST data; this UI change does not by itself guarantee a live tick-by-tick chart.
