# Legal notes for this app (read once)

This is not legal advice and it is not a guarantee. It lists where the app's data comes from, what each source allows, and
what keeps you on the safe side. Rules change: when in doubt, ask Upstox support in writing or a SEBI-registered professional.
Checked on 15 November 2026 (second check the same day: Upstox rate limits, live-stream limits, Gemini terms).

## The one rule that covers almost everything

**Use the app yourself, with your own Upstox account, and place your own orders by hand.**
Every serious risk below starts only when other people see the app's prices or its BUY signals, or when money changes hands.

## Where the data comes from

| Source | What the app takes | Status |
|---|---|---|
| Upstox API (your own login) | Live prices, candles, option chain, instrument list | Allowed for your own analysis. Upstox staff say API data may be used for analysis; they recommend that only Upstox customers consume it. Data APIs need no static IP. |
| NSE / niftyindices.com | Index constituent lists | The app no longer downloads anything from these sites (removed in v1.20.78). You may save a CSV by hand in your browser into `universes/`. |
| Google Gemini (optional) | Sends your strategy numbers, gets a text review | Your own API key and Google's terms. On the free tier Google may use what is sent to improve its products and asks you not to send sensitive, confidential or personal information - the app sends none (no username, password, token, key or account number; checked in v1.20.81). You must be 18 or older. |
| Anthropic API (optional, off unless you add a key) | Same as Gemini | Same as above. |
| TradingView Lightweight Charts | Chart drawing library (no data) | Free under Apache 2.0, on condition that the page shows TradingView's notice and a link. Added in v1.20.78. |
| TradingView webhook (optional) | Alerts you send from your own TradingView account | Your own alerts only. |
| Telegram / ntfy / email / phone push (optional) | Sends your alerts to you | Fine when they reach only you. See "Do not" below. |

## Upstox usage limits (the one thing the app itself must respect)

Upstox's published limits are per API, per user: 25 requests a second, 250 a minute and 1000 in 30 minutes; going over can get
API access suspended for a while. Since v1.20.81 the app counts its calls per API, stays under the per-second limit, never repeats a
request that got "too many requests" (429) and leaves that API alone for at least 20 seconds. The daily review warns when an API
passes 80 % of a limit or was refused. Every open terminal tab or device adds calls, so keep one open during market hours.
The live price stream uses 2 connections (NIFTY and BANKNIFTY); Upstox allows 2 per user for normal accounts. Do not run a second
copy of the app (or another Upstox streaming tool) with the same login at the same time.

## Do not

1. **Do not give other people a login, and do not share screenshots or a screen of live prices.** SEBI's circular of 24 May 2024
   stops exchanges and brokers from passing real-time price data to third parties without an agreement. Your Upstox data is
   licensed to you. From v1.20.78 new sign-ups are closed after the owner's account unless you set `ALLOW_EXTRA_USERS=1`.
2. **Do not send the BUY / SELL alerts to other people** - no Telegram group or channel, WhatsApp group, public ntfy topic or
   social media. SEBI treats buy / sell calls with targets and stop-loss as investment advice or research, whatever the medium,
   and has fined unregistered people for it. A disclaimer does not change that. Keep `TELEGRAM_CHAT_ID` your own private chat
   (a number without a minus sign; a minus sign means a group or channel) and choose an ntfy topic name nobody can guess.
3. **Do not charge for the app, its signals or the paper-trading feature**, and do not offer it as a service. That needs SEBI
   registration (Research Analyst / Investment Adviser) and a market-data licence from the exchange.
4. **Do not add automatic order placing without doing the paperwork first.** The app places no orders today. Since 1 April 2026
   every API order must come from a static IP registered with the broker, and faster strategies need an exchange Algo ID.
5. **Do not add automatic downloads from NSE websites.** Their terms of use were not checked for this note; exchange websites
   commonly forbid automated access, so the app simply does not do it.
6. **Do not publish your Upstox token, API secret or the Render address** in a public repository or chat.

## Things only you can check

* Your Upstox API app is in your own name and you have accepted the current Upstox API terms.
* If family members really must use it, each should have their own Upstox account and their own copy of the app.
* The Android APK is installed only on your own devices and is not uploaded to any app store or shared link.
* Profits from F&O trading are business income for income tax; keep your contract notes.

## What the app cannot promise

No software can be "100% legal and risk free" by itself: that depends on how it is used. Trading options can lose money; the
Diagnostics numbers are a replay of the past, not a promise.
