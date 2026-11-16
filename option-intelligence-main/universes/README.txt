Index lists for the Data Check "advance / decline" universe
===========================================================
From v1.20.66 the app does NOT download anything from NSE or niftyindices.com by itself. All market data comes from Upstox.

Built in (no file needed): Nifty 50 and Nifty Bank (12). They may be out of date after an index rebalance (Nifty 50 / Nifty Bank
are reviewed in March and September).

Nifty 100 and Nifty 500 have no built-in list. To use them (or to refresh the built-in ones):
1. Open niftyindices.com in your normal browser -> Indices -> the index -> "Index Constituents" CSV, and save the file.
2. Save it in this folder with exactly this name:
     ind_nifty50list.csv   ind_niftybanklist.csv   ind_nifty100list.csv   ind_nifty500list.csv
3. Commit and redeploy. A file here always wins over the built-in list; the Data Check page shows "Your CSV ... (saved date)".

Optional: UNIVERSE_ONLINE=1 lets the server fetch the CSVs itself again (then NSE's / niftyindices.com's terms for automated access apply).
UNIVERSE_DIR=/some/path points the app to another folder.
