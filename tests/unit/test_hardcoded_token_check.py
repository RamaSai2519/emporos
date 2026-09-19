import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_no_hardcoded_tokens.py"
_spec = importlib.util.spec_from_file_location("check_no_hardcoded_tokens", SCRIPT)
assert _spec and _spec.loader
_module = importlib.util.module_from_spec(_spec)
sys.modules["check_no_hardcoded_tokens"] = _module
_spec.loader.exec_module(_module)
StrategyTokenCheck = _module.StrategyTokenCheck


def _violations(tmp_path: Path, source: str) -> list[str]:
    (tmp_path / "strategy.py").write_text(source)
    return [str(v) for v in StrategyTokenCheck(tmp_path).violations()]


@pytest.mark.parametrize(
    "source",
    [
        'SYMBOL = "2885"\n',
        'resolve("500325")\n',
        "reliance_token = 2885\n",
        "subscribe(token=10099)\n",
        'instrument_token: int = 1333\nother = "1"\nx = 1\n',
        'token = "99926000"\n',
    ],
)
def test_hardcoded_tokens_are_flagged(tmp_path: Path, source: str) -> None:
    assert _violations(tmp_path, source)


@pytest.mark.parametrize(
    "source",
    [
        'resolver.by_symbol(Exchange.NSE, "RELIANCE-EQ")\n',
        "lookback = 1000\nperiod = 14\n",
        'label = "12"\n',
        "token = resolver.by_symbol(Exchange.NSE, symbol).token\n",
        "found = True\ntoken_ok = True\n",
        "def f(x: int) -> int:\n    return x * 252\n",
    ],
)
def test_legitimate_code_is_not_flagged(tmp_path: Path, source: str) -> None:
    assert _violations(tmp_path, source) == []


def test_violations_report_file_and_line(tmp_path: Path) -> None:
    (violation,) = _violations(tmp_path, "x = 1\ny = '2885'\n")

    assert violation.endswith("strategy.py:2: digit-only string literal '2885' looks like a token")


def test_the_real_strategy_package_is_clean() -> None:
    assert _module.main() == 0
