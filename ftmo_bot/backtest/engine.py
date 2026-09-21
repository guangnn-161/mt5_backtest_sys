# File: backtest/engine.py
import pandas as pd
import numpy as np


class BacktestEngine:
    def __init__(self, df: pd.DataFrame, strategy, risk_manager, compliance_guard):
        self.df = df
        self.strategy = strategy
        self.risk_manager = risk_manager
        self.compliance_guard = compliance_guard

        self.initial_balance = compliance_guard.initial_balance
        self.balance = self.initial_balance
        self.equity = self.initial_balance

    def run(self) -> pd.DataFrame:
        trades = []
        equity_curve = []

        open_trade = None
        peak_equity = self.initial_balance
        daily_start_equity = self.initial_balance
        current_day = None

        first_fail_index = None
        first_fail_time = None
        first_fail_reason = None
        is_failed = False
        trading_halted = False  # Bug #3c fix: ngừng MỞ lệnh mới sau khi vi phạm

        for i, row in self.df.iterrows():
            timestamp = row['time']
            day = timestamp.date()

            if current_day != day:
                current_day = day
                daily_start_equity = self.equity  # reset theo equity, không phải balance

        # 1. Quản lý lệnh đang mở (kiểm tra SL/TP) — logic giữ nguyên như cũ
            if open_trade is not None:
                hit_tp = False
                hit_sl = False
                exit_price = row['close']

                if open_trade['type'] == 'BUY':
                    if row['high'] >= open_trade['tp']:
                        hit_tp = True
                        exit_price = open_trade['tp']
                    elif row['low'] <= open_trade['sl']:
                        hit_sl = True
                        exit_price = open_trade['sl']
                elif open_trade['type'] == 'SELL':
                    if row['low'] <= open_trade['tp']:
                        hit_tp = True
                        exit_price = open_trade['tp']
                    elif row['high'] >= open_trade['sl']:
                        hit_sl = True
                        exit_price = open_trade['sl']

                if hit_tp or hit_sl:
                    price_diff = (exit_price - open_trade['entry']) if open_trade['type'] == 'BUY' else (
                        open_trade['entry'] - exit_price)
                    pnl_usd = price_diff * open_trade['size'] * 100

                # Bug #3b fix: % tính trên balance TRƯỚC lệnh này, không phải initial_balance cố định.
                # Điều này làm pnl_pct nhất quán với cách monte_carlo.py mô phỏng compounding.
                    balance_before_trade = self.balance
                    self.balance += pnl_usd
                    pnl_pct = (pnl_usd / balance_before_trade) * 100

                    open_trade['exit_time'] = timestamp
                    open_trade['exit_price'] = exit_price
                    open_trade['pnl_usd'] = pnl_usd
                    open_trade['pnl_pct'] = pnl_pct
                    open_trade['balance'] = self.balance
                    trades.append(open_trade)

                    open_trade = None

        # 2. Mark-to-market equity — Bug #3a fix: tính cả lãi/lỗ nổi của lệnh đang mở
            if open_trade is not None:
                if open_trade['type'] == 'BUY':
                    floating_pnl = (
                        row['close'] - open_trade['entry']) * open_trade['size'] * 100
                else:
                    floating_pnl = (
                        open_trade['entry'] - row['close']) * open_trade['size'] * 100
                self.equity = self.balance + floating_pnl
            else:
                self.equity = self.balance

               # 3'. Xác định tầng trạng thái hiện tại (Safe / Caution / Critical / Violated)
            trading_state = self.compliance_guard.get_trading_state(
                current_equity=self.equity,
                daily_start_equity=daily_start_equity,
                peak_equity=peak_equity
            )

            # Tầng Critical: chủ động đóng lệnh đang mở NGAY, không chờ SL bị chạm
            if trading_state == "critical" and open_trade is not None:
                exit_price = row['close']
                price_diff = (exit_price - open_trade['entry']) if open_trade['type'] == 'BUY' else (
                    open_trade['entry'] - exit_price)
                pnl_usd = price_diff * open_trade['size'] * 100

                balance_before_trade = self.balance
                self.balance += pnl_usd
                pnl_pct = (pnl_usd / balance_before_trade) * 100

                open_trade['exit_time'] = timestamp
                open_trade['exit_price'] = exit_price
                open_trade['pnl_usd'] = pnl_usd
                open_trade['pnl_pct'] = pnl_pct
                open_trade['balance'] = self.balance
                # đánh dấu để phân tích sau
                open_trade['closed_by'] = 'critical_soft_stop'
                trades.append(open_trade)

                self.equity = self.balance
                open_trade = None
                print(f"[~] Soft-stop CRITICAL tại {timestamp}: chủ động đóng lệnh, "
                    f"equity={self.equity:.2f}")

            # Tìm tín hiệu vào lệnh mới — chỉ khi Safe (không mở thêm lệnh ở Caution/Critical/Violated)
            if open_trade is None and trading_state == "safe" and not trading_halted:
                signal = self.strategy.generate_signal(row)
                if signal:
                    risk_size = self.risk_manager.calculate_position_size(
                        self.balance, signal['entry'], signal['sl'])
                    if risk_size > 0:
                        open_trade = {
                            'entry_time': timestamp,
                            'type': signal['type'],
                            'entry': signal['entry'],
                            'sl': signal['sl'],
                            'tp': signal['tp'],
                            'size': risk_size
                        }

        # 4. Cập nhật đỉnh equity + kiểm tra tuân thủ — dùng EQUITY, không phải balance
            if self.equity > peak_equity:
                peak_equity = self.equity

            violated, reason = self.compliance_guard.check_violation(
                current_equity=self.equity,
                daily_start_equity=daily_start_equity,
                peak_equity=peak_equity
            )

            if violated and not is_failed:
                is_failed = True
                trading_halted = True
                first_fail_index = len(trades)
                first_fail_time = timestamp
                first_fail_reason = reason
                print(f"[!] CẢNH BÁO FTMO: Vi phạm tại mốc lệnh thứ {first_fail_index} "
                      f"({timestamp})! Lý do: {reason}")
                print("[~] Ngừng mở lệnh mới từ đây. Vẫn ghi log phần còn lại "
                      "để vẽ 'stress-test zone' trên dashboard.")

            equity_curve.append({
                'time': timestamp,
                'equity': self.equity,
                'is_failed': is_failed
            })

        df_trades = pd.DataFrame(trades)
        if not df_trades.empty:
            df_trades.attrs['first_fail_index'] = first_fail_index
            df_trades.attrs['first_fail_time'] = first_fail_time
            df_trades.attrs['first_fail_reason'] = first_fail_reason
            df_trades.attrs['equity_curve'] = pd.DataFrame(equity_curve)
        else:
            df_trades.attrs['first_fail_index'] = None
            df_trades.attrs['first_fail_time'] = first_fail_time
            df_trades.attrs['first_fail_reason'] = first_fail_reason
            df_trades.attrs['equity_curve'] = pd.DataFrame(equity_curve)

        return df_trades
