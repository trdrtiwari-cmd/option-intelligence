import subprocess
from pathlib import Path
SH = Path(__file__).resolve().parents[2] / "oracle-setup.sh"


def test_oracle_setup_script():
    if not SH.exists():
        return
    assert subprocess.run(["bash", "-n", str(SH)]).returncode == 0
    s = SH.read_text(encoding="utf-8")
    for must in ("postgresql", "acc.copy_to(", "oi-autoupdate.sh", "-le 1545", "oi-backup.sh", "Asia/Kolkata", "KEEPALIVE=0"):
        assert must in s, must
