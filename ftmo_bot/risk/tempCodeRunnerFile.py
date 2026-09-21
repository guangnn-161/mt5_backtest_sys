
            dd_usd = self.initial_balance - current_equity

        total_dd_pct = (dd_usd / self.initial_balance) * 100

        if total_dd_pct >= self.max_total_dd_pct:
            return True, (f"Vi phạm Max Total Drawdown ({self.drawdown_