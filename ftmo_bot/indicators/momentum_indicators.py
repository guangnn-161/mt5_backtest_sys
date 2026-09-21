import pandas as pd


def add_ema(df: pd.DataFrame, period: int = 9, column_name: str = 'ema') -> pd.DataFrame:
    df[column_name] = df['close'].ewm(span=period, adjust=False).mean()
    return df


def add_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    ema_fast = df['close'].ewm(span=fast, adjust=False).mean()
    ema_slow = df['close'].ewm(span=slow, adjust=False).mean()
    df['macd_line'] = ema_fast - ema_slow
    df['macd_signal'] = df['macd_line'].ewm(span=signal, adjust=False).mean()
    df['macd_hist'] = df['macd_line'] - df['macd_signal']
    return df
