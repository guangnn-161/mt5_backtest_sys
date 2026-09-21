import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime
import pytz
from pathlib import Path


def fetch_mt5_data(symbol: str, timeframe, num_bars: int):
    # 1. Khởi tạo kết nối với MT5 terminal đang chạy
    if not mt5.initialize():
        print(f"Khởi tạo MT5 thất bại, mã lỗi: {mt5.last_error()}")
        quit()

    # [BỔ SUNG QUAN TRỌNG]: Ép terminal bật mã này vào Market Watch
    if not mt5.symbol_select(symbol, True):
        print(
            f"Không thể bật {symbol} trong Market Watch. Mã không tồn tại hoặc sai hậu tố.")
        mt5.shutdown()
        return

    print(f"Đã kết nối MT5. Bắt đầu tải {num_bars} nến {symbol}...")

    # 2. Tải dữ liệu từ nến hiện tại (pos=0) lùi về quá khứ
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, num_bars)
    mt5.shutdown()

    if rates is None or len(rates) == 0:
        print(
            f"Không thể lấy dữ liệu cho {symbol}. Hãy kiểm tra lại kết nối mạng của MT5.")
        return

    # 3. Chuyển đổi dữ liệu thô sang Pandas DataFrame
    df = pd.DataFrame(rates)
    # MT5 exposes Unix epoch seconds. Preserve UTC explicitly so the backtester
    # can convert daily reset boundaries without guessing the source timezone.
    df['time'] = pd.to_datetime(df['time'], unit='s', utc=True)

    # 4. Định hình đường dẫn lưu file theo chuẩn kiến trúc
    project_root = Path(__file__).parent.parent
    save_dir = project_root / "data" / "raw"
    save_dir.mkdir(parents=True, exist_ok=True)

    file_name = f"{symbol.lower()}_m5.csv"
    save_path = save_dir / file_name

    # 5. Lưu ra file CSV
    df.to_csv(save_path, index=False)
    print(f"[*] Thành công! Đã lưu {len(df)} nến vào: {save_path}")
    print("[*] Sẵn sàng cho việc backtest nội bộ.")


if __name__ == "__main__":
    SYMBOL = "XAUUSDm"
    TIMEFRAME = mt5.TIMEFRAME_M5
    NUMBER_OF_BARS = 200000

    fetch_mt5_data(SYMBOL, TIMEFRAME, NUMBER_OF_BARS)
