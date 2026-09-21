# Backtest integrity assumptions

The engine uses deliberately conservative rules where M5 OHLC data cannot reveal
the true intrabar path.

## Execution model

- A signal is calculated only after a candle closes.
- The order is filled at the next candle's open, never at the signal candle's
  already-known close.
- Configured spread and entry slippage are applied adversely to the fill.
- Configured exit slippage and commission are included in the trade ledger.
- If SL and TP are both touched in one candle, SL is assumed to occur first.
- A gap through SL is filled at the opening price plus adverse slippage.
- A position still open on the final candle is closed and marked `end_of_data`.

Default XAUUSD cost assumptions live in `ftmo_bot/configs/risk_params.yaml`.
They are examples, not broker facts. Calibrate point size, contract size, spread,
slippage, and commission against the exact symbol and account being tested.

## Time and compliance

- Rule semantics are based on FTMO's published
  [Trading Objectives](https://ftmo.com/en/trading-objectives/); the checked-in
  5% daily / 10% total configuration models the 2-Step rules.
- MT5 Unix timestamps exported by `download_mt5_data.py` are saved as UTC.
- Trading-day boundaries are converted to `Europe/Prague`, including daylight
  saving time.
- The daily loss floor is anchored to balance at 00:00 CE(S)T and monitored
  against current equity, including floating P/L and commissions.
- `INTERNAL STOP` means the configured safety buffer was reached.
- `HARD BREACH` means the unbuffered FTMO limit was reached. Reports never label
  an internal stop as an FTMO failure.
- Compliance checks include adverse intrabar equity, not only candle-close
  balance.

## Robustness estimates

- The rolling-window module is a robustness test, not walk-forward optimization;
  it does not optimize parameters on a training set.
- Indicator warm-up history is supplied before every rolling test window, but
  the engine cannot trade during warm-up.
- Monte Carlo samples contiguous blocks of real trading days. It preserves the
  order and number of trades within each sampled day and does not invent a fixed
  `trades_per_day` reset.

OHLC backtests still cannot reconstruct tick order, variable historical spread,
partial fills, latency, swaps, market impact, or news-time execution. Tick data
and broker-specific cost history are required before treating results as live
performance evidence.
