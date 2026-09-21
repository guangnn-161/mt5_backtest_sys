# File: risk/risk_manager.py
import yaml
from math import floor


class RiskManager:
    def __init__(self, risk_config_path: str):
        with open(risk_config_path, 'r', encoding='utf-8') as f:
            self.risk_params = yaml.safe_load(f)

        self.risk_per_trade_pct = self.risk_params["risk_per_trade_pct"]
        self.max_open_risk_pct = self.risk_params["max_open_risk_pct"]
        self.max_lot_size = self.risk_params.get("max_lot_size", 5.0)

    def calculate_position_size(self, current_equity: float, entry_price: float,
                                stop_loss: float, contract_size: float = 100,
                                current_open_risk_usd: float = 0.0,
                                additional_risk_per_lot_usd: float = 0.0) -> float:
        if entry_price == stop_loss:
            return 0.0
        if contract_size <= 0 or additional_risk_per_lot_usd < 0:
            raise ValueError("Contract size must be positive and extra risk non-negative")

        per_trade_risk = current_equity * (self.risk_per_trade_pct / 100)
        max_open_risk = current_equity * (self.max_open_risk_pct / 100)
        available_open_risk = max(0.0, max_open_risk - current_open_risk_usd)
        risk_amount_usd = min(per_trade_risk, available_open_risk)
        sl_distance = abs(entry_price - stop_loss)
        risk_per_lot = contract_size * sl_distance + additional_risk_per_lot_usd
        raw_lot = risk_amount_usd / risk_per_lot

        # Nếu lot thô < mức tối thiểu sàn (0.01), KHÔNG ép lên —
        # ép lên sẽ làm risk thực tế của lệnh vượt risk_per_trade_pct đã cấu hình.
        # Bỏ qua lệnh này (coi như risk quá nhỏ để giao dịch có kiểm soát) thay vì chấp nhận
        # risk cao hơn dự tính.
        if raw_lot < 0.01:
            return 0.0

        # Always round down so lot-step rounding cannot exceed the risk budget.
        lot_size = floor(raw_lot * 100) / 100
        lot_size = min(lot_size, self.max_lot_size)
        return lot_size

    def evaluate(self, account_state: dict, signal: dict, compliance_guard) -> dict:
        """
        THAY ĐỔI INTERFACE: account_state giờ cần đủ 3 key: 'equity',
        'daily_start_balance', 'peak_equity' — không chỉ 'equity' như bản cũ.
        Lý do: ComplianceGuard.evaluate_entry cần cả 3 giá trị này để tính daily loss
        và max drawdown (xem Bug #1, Bug #3).
        """
        allowed, reason = compliance_guard.evaluate_entry(
            current_equity=account_state['equity'],
            daily_start_balance=account_state['daily_start_balance'],
            peak_equity=account_state['peak_equity'],
        )
        if not allowed:
            return {"allowed": False, "lot_size": 0.0, "reason": reason}

        lot_size = self.calculate_position_size(
            current_equity=account_state['equity'],
            entry_price=signal['entry'],
            stop_loss=signal['sl']
        )

        if lot_size <= 0:
            return {
                "allowed": False,
                "lot_size": 0.0,
                "reason": "Risk sizing trả về 0 (SL quá rộng so với risk_per_trade_pct)"
            }

        return {"allowed": True, "lot_size": lot_size, "reason": "OK"}
