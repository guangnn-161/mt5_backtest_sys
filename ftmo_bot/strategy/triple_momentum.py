from strategy.base import BaseStrategy
from indicators.oscillators import add_rsi
from indicators.momentum_indicators import add_ema, add_macd
import pandas as pd

class TripleMomentumStrategy(BaseStrategy):
    supported_asset_classes = ('metal',)
    supported_timeframes = ('M5', 'M15')
    def __init__(self, params: dict = None):
        default_params = {
            'rsi_min': 55,
            'rsi_max': 70,
            'start_hour': 8,   # Bắt đầu từ phiên London (bỏ qua phiên Á đi ngang)
            'end_hour': 20     # Kết thúc trước phiên Mỹ quá hoảng loạn nếu muốn an toàn
        }
        if params:
            default_params.update(params)
            
        super().__init__(default_params)
        self.prev_hist = None

    def prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        # Nạp đồng thời 3 chỉ báo theo dạng vector hóa siêu tốc
        df = add_rsi(df, period=14, column_name='rsi')
        df = add_ema(df, period=9, column_name='ema_fast')
        df = add_ema(df, period=21, column_name='ema_slow')
        df = add_macd(df, fast=12, slow=26, signal=9)
        return df
        
    def generate_signal(self, row):
        # Lấy giá trị các chỉ báo tại cây nến hiện tại
        rsi = getattr(row, 'rsi', None)
        ema_fast = getattr(row, 'ema_fast', None)
        ema_slow = getattr(row, 'ema_slow', None)
        hist = getattr(row, 'macd_hist', None)
        
        if any(pd.isna(x) for x in [rsi, ema_fast, ema_slow, hist]):
            return None
            
        if self.prev_hist is None:
            self.prev_hist = hist
            return None
            
        hist_delta = hist - self.prev_hist
        self.prev_hist = hist
        
        # 1. Lọc khung giờ (Ví dụ: Chỉ đánh phiên London/Mỹ khi thanh khoản mạnh)
        if not (self.params['start_hour'] <= row.time.hour < self.params['end_hour']):
            return None
            
        # 2. ĐIỀU KIỆN ĐỒNG THUẬN 3 CHỈ BÁO (BUY MOMENTUM)
        cond_trend = ema_fast > ema_slow                 # Điều kiện 1: EMA cắt lên / xu hướng tăng
        cond_macd = (hist > 0) and (hist_delta > 0)     # Điều kiện 2: MACD Histogram dương và đang nở ra
        cond_rsi = self.params['rsi_min'] < rsi < self.params['rsi_max'] # Điều kiện 3: RSI nằm trong vùng bứt phá
        
        if cond_trend and cond_macd and cond_rsi:
            return {
                'type': 'BUY',
                'entry': row.close,
                'sl': row.close - 4.0,  # Khoảng cắt lỗ thoáng hơn cho sóng Momentum (40 pips)
                'tp': row.close + 8.0   # Chốt lời tỷ lệ R:R = 1:2 (80 pips)
            }
            
        return None
