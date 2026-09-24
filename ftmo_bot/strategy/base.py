# File: strategy/base.py
import pandas as pd


class BaseStrategy:
    """Market-agnostic alpha contract.

    ``generate_signal`` runs after a completed bar.  It returns an order intent,
    never a filled trade.  The execution runtime owns fills, costs, positions,
    and FTMO compliance, so this exact strategy can later be reused in paper or
    MT5 live mode.
    """
    def __init__(self, params: dict):
        self.params = params

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Được gọi 1 lần TRƯỚC khi backtest bắt đầu.
        Nơi chiến lược khai báo và tính toán sẵn các cột Indicator nó cần.
        Mặc định trả về df nguyên bản.
        """
        return df

    def generate_signal(self, row) -> dict:
        raise NotImplementedError("Phải ghi đè hàm này ở class con")

    @staticmethod
    def order_intent(side: str, entry: float, sl: float, tp: float, *,
                     order_type: str = "MARKET", valid_for_bars: int | None = None,
                     tag: str | None = None) -> dict:
        """Build a broker-independent market, limit, or stop order intent.

        A pending order created on bar N can first fill on bar N+1.  For LIMIT
        and STOP, ``entry`` is the trigger price.  ``valid_for_bars=None`` keeps
        the order active until filled/cancelled; use a small positive number for
        time-limited setups.
        """
        return {
            "type": side.upper(), "order_type": order_type.upper(),
            "entry": float(entry), "sl": float(sl), "tp": float(tp),
            "valid_for_bars": valid_for_bars, "tag": tag,
        }
