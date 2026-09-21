import os
import yaml
from pathlib import Path

def create_project_structure(base_dir="ftmo_bot"):
    # 1. Khởi tạo danh sách thư mục theo Handoff Document
    directories = [
        "configs",
        "data/raw",
        "strategy",
        "risk",
        "backtest",
        "tools",
        "mql5_ea",
        "logs",
        "reports"
    ]
    
    for d in directories:
        Path(os.path.join(base_dir, d)).mkdir(parents=True, exist_ok=True)
        # Tạo __init__.py cho các module Python
        if d in ["strategy", "risk", "backtest", "tools"]:
            open(os.path.join(base_dir, d, "__init__.py"), 'a').close()

    # 2. Tạo configs/ftmo_rules.yaml (Dành cho Standard 10k - Phase 1)
    ftmo_rules = {
        "account_size": 10000,
        "currency": "USD",
        "max_daily_loss_pct": 5.0,
        "max_daily_loss_usd": 500,
        "max_total_loss_pct": 10.0,
        "max_total_loss_usd": 1000,
        "profit_target_pct": 10.0,
        "profit_target_usd": 1000,
        "drawdown_type": "static_from_initial", # Tính từ initial balance
        "data_timezone": "UTC",
        "daily_reset_timezone": "Europe/Prague"
    }
    with open(os.path.join(base_dir, "configs/ftmo_rules.yaml"), 'w') as f:
        yaml.dump(ftmo_rules, f, default_flow_style=False, sort_keys=False)

    # 3. Tạo configs/risk_params.yaml (Thiết lập Buffer an toàn)
    risk_params = {
        "risk_per_trade_pct": 0.5, # Rủi ro mỗi lệnh: 0.5%
        "max_open_risk_pct": 1.5,  # Tổng rủi ro tối đa tại 1 thời điểm
        "daily_loss_buffer_pct": 1.0, # Buffer: dừng trade khi loss chạm 4% (5% - 1%)
        "total_loss_buffer_pct": 1.5, # Buffer: dừng hoàn toàn khi total loss chạm 8.5%
        "caution_threshold_ratio": 0.6,
        "critical_threshold_ratio": 0.9,
        "execution": {
            "contract_size": 100,
            "point_size": 0.01,
            "spread_points": 20,
            "slippage_points": 5,
            "commission_per_lot_round_turn_usd": 0.0,
            "intrabar_policy": "stop_first",
        },
    }
    with open(os.path.join(base_dir, "configs/risk_params.yaml"), 'w') as f:
        yaml.dump(risk_params, f, default_flow_style=False, sort_keys=False)

    # 4. Tạo configs/strategy_params.yaml (Chuẩn bị cho Momentum XAUUSD M5)
    strategy_params = {
        "symbol": "XAUUSD",
        "timeframe": "M5",
        "sessions": {
            "asian": {"enable": True, "start": "00:00", "end": "08:00"}, # Giờ server ảo
            "london": {"enable": True, "start": "08:00", "end": "14:30"},
            "new_york": {"enable": False, "start": "14:30", "end": "23:59"} # Né phiên Mỹ
        },
        "momentum_params": {
            "body_threshold": 2.0,
            "stop_distance": 5.0,
            "target_distance": 10.0,
        }
    }
    with open(os.path.join(base_dir, "configs/strategy_params.yaml"), 'w') as f:
        yaml.dump(strategy_params, f, default_flow_style=False, sort_keys=False)

    # 5. Tạo file tools/experiment_logger.py
    logger_code = '''import csv
import json
import uuid
import subprocess
from datetime import datetime
from pathlib import Path

LOG_FILE = Path(__file__).parent.parent / "experiments_log.csv"

def get_git_commit():
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"]).decode("utf-8").strip()
    except Exception:
        return "no_git"

def log_experiment(params: dict, metrics: dict, notes: str = ""):
    is_new = not LOG_FILE.exists()
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    
    with open(LOG_FILE, 'a', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        if is_new:
            writer.writerow(["run_id", "timestamp_utc", "git_commit", "params_json", "metrics_json", "notes"])
            
        writer.writerow([
            run_id,
            datetime.utcnow().isoformat(),
            get_git_commit(),
            json.dumps(params),
            json.dumps(metrics),
            notes
        ])
    print(f"[*] Logged experiment {run_id}")
    return run_id
'''
    with open(os.path.join(base_dir, "tools/experiment_logger.py"), 'w', encoding='utf-8') as f:
        f.write(logger_code)

    # 6. Tạo file research_journal.md theo template
    journal_template = '''# Research Journal

## Cấu trúc chuẩn (Viết TRƯỚC khi test)
- **Run ID liên kết:** 
- **Giả thuyết:** 
- **Thay đổi cụ thể:** 
- **Kết quả:** 
- **Diễn giải:** 
- **Quyết định:** [ ] Giữ  [ ] Revert  [ ] Cần test thêm
- **Bước tiếp theo:** 

---
## Entry #001 - YYYY-MM-DD
*(Chuẩn bị test chiến lược XAUUSD M5 Momentum)*
'''
    with open(os.path.join(base_dir, "research_journal.md"), 'w', encoding='utf-8') as f:
        f.write(journal_template)
        
    # 7. Tạo file .gitignore
    gitignore = '''data/
logs/
reports/
__pycache__/
*.pyc
.env
'''
    with open(os.path.join(base_dir, ".gitignore"), 'w') as f:
        f.write(gitignore)

    print(f"Hạ tầng đã được khởi tạo thành công tại thư mục: ./{base_dir}")
    print("Vui lòng khởi tạo git repo: cd ftmo_bot && git init")

if __name__ == "__main__":
    create_project_structure()
