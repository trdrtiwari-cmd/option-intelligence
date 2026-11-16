"""v1.20.71: blocker labels are not cut inside brackets / clock times; previous-session signal time carries its day."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import signal_board as sb
from signal_fusion import blocker_label


def test_blocker_label_keeps_brackets_and_times():
    assert blocker_label("Reconfirmed (60s hold: setup persists, price holds): 12s of 60s") == "Reconfirmed (60s hold: setup persists, price holds)"
    assert blocker_label("Entry time window 09:45–12:00, 14:30–15:00 IST: Now 15:46 IST · market closed") == "Entry time window 09:45–12:00, 14:30–15:00 IST"
    assert blocker_label("Profit check: Room to the target (≥ 1.5 × risk and ≥ 1.2 ATR): only 1.1 ATR") == "Profit check: Room to the target (≥ 1.5 × risk and ≥ 1.2 ATR)"
    assert blocker_label("Profit check: Premium reward: too small") == "Profit check: Premium reward"
    assert blocker_label("Score ≥ 60") == "Score ≥ 60"


def test_board_blocked_by_uses_full_label():
    final = {"blockers": ["Reconfirmed (60s hold: setup persists, price holds): 12s of 60s"]}
    assert sb.blocked_by({"gates": []}, final) == "Reconfirmed (60s hold: setup persists, price holds)"
