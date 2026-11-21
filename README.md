# CS skin prices

Daily prices for the skins of a small offline case-opening game.

- `prices.json` - `{"date", "markets", "p": {id: [FN, MW, FT, WW, BS]}}`, price in cents (USD*100),
  median of the cheapest listing on Buff163, CSFloat and Skinport.
- `history.json` - `{"days": [...], "w": {id: wear index}, "p": {id: [price per day]}}`, last 90 days, 0 = no data.

Source data: [prices.csgotrader.app](https://prices.csgotrader.app) (Buff163, CSFloat, Skinport) and the
Skinport API for Doppler phases. Updated by GitHub Actions every day at 10:40 UTC.
