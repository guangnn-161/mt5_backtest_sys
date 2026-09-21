import csv
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
