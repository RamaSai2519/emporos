import logging

import pytest

from emporos.core.alerts import LogAlertSink


def test_an_alert_is_written_as_an_error_log(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="emporos.alerts"):
        LogAlertSink().raise_alert("instrument_master_rejected", "row count off by 40%")

    assert [r.levelno for r in caplog.records] == [logging.ERROR]
    assert "instrument_master_rejected" in caplog.text
    assert "row count off by 40%" in caplog.text
