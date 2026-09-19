"""The determinism lint (plan.md §9): strategy code may not read the wall clock, use unseeded
randomness, or perform I/O. Two halves: the scanner is proven on bad samples, then pointed at the
real package."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_strategy_purity.py"
_spec = importlib.util.spec_from_file_location("check_strategy_purity", SCRIPT)
assert _spec and _spec.loader
_module = importlib.util.module_from_spec(_spec)
sys.modules["check_strategy_purity"] = _module
_spec.loader.exec_module(_module)
StrategyPurityCheck = _module.StrategyPurityCheck


def _violations(tmp_path: Path, source: str) -> list[str]:
    (tmp_path / "strategy.py").write_text(source)
    return [str(v) for v in StrategyPurityCheck(tmp_path).violations()]


@pytest.mark.parametrize(
    "source",
    [
        "import datetime\nx = datetime.datetime.now()\n",
        "from datetime import datetime\nx = datetime.now()\n",
        "from datetime import datetime\nx = datetime.utcnow()\n",
        "from datetime import date\nx = date.today()\n",
        "from datetime import datetime\nx = datetime.fromtimestamp(0)\n",
    ],
)
def test_wall_clock_reads_are_flagged(tmp_path: Path, source: str) -> None:
    assert any("wall clock" in v for v in _violations(tmp_path, source))


@pytest.mark.parametrize(
    "source",
    [
        "import random\nx = random.random()\n",
        "import random\nx = random.choice([1, 2])\n",
        "import random\nrandom.seed(1)\n",
        "from random import randint\n",
        "import random\nx = random.SystemRandom()\n",
    ],
)
def test_unseeded_randomness_is_flagged(tmp_path: Path, source: str) -> None:
    assert _violations(tmp_path, source)


@pytest.mark.parametrize(
    "source",
    [
        "import os\n",
        "import time\n",
        "import asyncio\n",
        "import subprocess\n",
        "import socket\n",
        "import sqlite3\n",
        "import requests\n",
        "import httpx\n",
        "import yaml\n",
        "from pathlib import Path\n",
        "from urllib.request import urlopen\n",
        "import pymongo\n",
        "x = open('f.txt')\n",
        "print('hello')\n",
        "x = input()\n",
        "exec('1')\n",
        "x = __import__('os')\n",
    ],
)
def test_io_and_side_effects_are_flagged(tmp_path: Path, source: str) -> None:
    assert _violations(tmp_path, source)


@pytest.mark.parametrize(
    "source",
    [
        "import random\nrng = random.Random(7)\nx = rng.random()\n",
        "from random import Random\nrng = Random('seed')\n",
        "def f(ctx):\n    return ctx.clock.now()\n",  # the sanctioned clock: a different receiver
        "from datetime import datetime, UTC\nx = datetime(2026, 1, 1, tzinfo=UTC)\n",
        "import logging\nlogging.getLogger('x').info('ok')\n",
        "from decimal import Decimal\nx = Decimal('1.5')\n",
        "from emporos.domain.money import Money\n",
        "from pydantic import BaseModel\n",
        "from . import sibling\n",
        "import hashlib, json\n",
    ],
)
def test_clean_code_passes(tmp_path: Path, source: str) -> None:
    assert _violations(tmp_path, source) == []


def test_a_violation_names_its_file_and_line(tmp_path: Path) -> None:
    (violation,) = _violations(tmp_path, "x = 1\nimport time\n")
    assert "strategy.py:2" in violation


def test_the_real_strategy_package_is_pure() -> None:
    root = Path(__file__).resolve().parents[3] / "src" / "emporos" / "strategies"
    assert list(root.rglob("*.py")), "the scanner would pass vacuously"
    assert StrategyPurityCheck().violations() == []
