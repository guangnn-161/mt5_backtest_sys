# Strategy and execution runtime design

## Goal

One alpha implementation must move through three environments without changing
its decision logic:

```text
Parquet historical data -> simulated broker -> backtest report
MT5 realtime data      -> paper broker     -> demo ledger
MT5 realtime data      -> MT5 broker       -> broker orders
```

An alpha decides **whether and how it wants to enter**.  It does not decide that
it has filled, calculate PnL, or call MetaTrader.

## Strategy contract

Every strategy derives from `BaseStrategy` and implements:

```python
prepare_data(df)       # indicators from current/past data only
generate_signal(bar)   # called after that bar has closed
```

`generate_signal` returns an order intent. Existing strategy dictionaries remain
compatible: without `order_type`, they mean `MARKET`.

```python
return self.order_intent(
    side="BUY",
    order_type="LIMIT",       # MARKET | LIMIT | STOP
    entry=entry_price,
    sl=stop_loss,
    tp=take_profit,
    valid_for_bars=3,          # null means GTC in the historical engine
    tag="pullback-v1",
)
```

An intent created from the close of bar `N` first becomes eligible on bar `N+1`.
This prevents look-ahead bias.

## Historical OHLC execution policy

OHLC bars cannot reveal every tick path, queue position, or spread change. The
backtest therefore makes only explicit, conservative claims:

| Intent | Trigger on a future bar | Gap rule |
|---|---|---|
| `MARKET` | fills at next bar open | open plus adverse spread/slippage |
| `BUY LIMIT` | `low <= limit <= high` | fills at limit, never receives price improvement |
| `SELL LIMIT` | `low <= limit <= high` | fills at limit, never receives price improvement |
| `BUY STOP` | `high >= stop` | if open is already above stop, fills at open |
| `SELL STOP` | `low <= stop` | if open is already below stop, fills at open |

After a position is opened, if both protective stop and target appear reachable
within the same OHLC bar, the engine records the stop. This is deliberately
pessimistic. Tick validation is required before trusting an intrabar strategy.

The current portfolio policy permits one open position and one pending entry;
`execution.max_pending_orders` is the controlled extension point. Do not raise
it before portfolio-level open-risk accounting supports multiple positions.

## How to create candidates without data-mining

1. Write one economic/market hypothesis first, e.g. “after an EMA trend, an ATR
   pullback has continuation.” Do not begin by sweeping indicators.
2. Specify entry type before evaluating results: market for immediate momentum,
   limit for pullback/reversion, stop for breakout confirmation.
3. Use a small parameter grid based on the hypothesis. Keep a fixed untouched
   out-of-sample period.
4. Reject a candidate if its edge only appears after one exact parameter choice,
   one calendar period, or one execution assumption.
5. Run rolling windows, walk-forward, and constrained Monte Carlo. The current
   report uses the no-loss path for research tables and the FTMO-constrained path
   for equity comparison and Monte Carlo.
6. Promote only a stable candidate to MT5 demo. The signal code must be unchanged;
   only its data/execution runtime changes.

## Required evidence before MT5 demo

- Data fingerprint, config hash, strategy version and execution configuration.
- Order lifecycle audit: submitted, filled, expired, rejected-risk.
- Reproducible result under the same Parquet snapshot.
- Tick-aware validation for any alpha whose outcome depends on intrabar order.
- A hard risk kill-switch and reconciliation design before a real account.
