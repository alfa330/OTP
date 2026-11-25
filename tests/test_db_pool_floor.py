"""Пол пула соединений: 16 — одному процессу на всё, 4 — раздельным ролям в кластере.

25.11.2026: пять подов (api ×2, realtime ×2, worker) × 16 = 80 = лимит пользователя базы, и при
выкладке новые поды не получили ни одного соединения. Значение задаётся при импорте db.core —
проверяем в отдельном процессе с нужным окружением."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _floor(env_extra):
    env = {k: v for k, v in os.environ.items() if k not in ('OTP_ROLE', 'POSTGRES_POOL_MIN_CONN', 'POSTGRES_POOL_MAX_CONN')}
    env.update(env_extra)
    # db.core зовёт time.tzset() при импорте — на Windows его нет, подставляем пустышку.
    code = ("import time; time.tzset = getattr(time, 'tzset', lambda: None); "
            "import db.core as c; print(c.MIN_CONN, c.MAX_CONN)")
    out = subprocess.run([sys.executable, '-c', code],
                         cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    return tuple(int(x) for x in out.stdout.split()[-2:])


@pytest.mark.parametrize('role, expected', [(None, 16), ('all', 16), ('api', 4), ('realtime', 4), ('worker', 4)])
def test_default_floor_by_role(role, expected):
    env = {} if role is None else {'OTP_ROLE': role}
    assert _floor(env)[0] == expected


def test_env_still_wins():
    assert _floor({'OTP_ROLE': 'api', 'POSTGRES_POOL_MIN_CONN': '7', 'POSTGRES_POOL_MAX_CONN': '20'}) == (7, 20)


def test_five_split_pods_leave_room_for_a_rollout():
    """Пять подов плюс три новых при выкладке укладываются в лимит пользователя базы (80)."""
    floor = _floor({'OTP_ROLE': 'api'})[0]
    assert (5 + 3) * floor <= 80
