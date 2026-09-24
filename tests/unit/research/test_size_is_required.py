"""EM-191 F4: no study or curation can be built without a declared size.

Cost is a function of size, so a result without one is not comparable with any other. The type
checker already refuses a call that omits it; these tests pin that `size` stays a required
parameter (nobody gives it a convenient default later)."""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from emporos.backtest.experiment_report import CurationExperimentReportBuilder
from emporos.research.cross_sectional import CrossSectionalEngine
from emporos.research.engine import AlphaDiscoveryEngine
from emporos.research.lead_lag import LeadLagEngine


@pytest.mark.parametrize(
    "constructor",
    [AlphaDiscoveryEngine, LeadLagEngine, CrossSectionalEngine, CurationExperimentReportBuilder],
)
def test_the_size_parameter_is_required(constructor: Any) -> None:
    parameter = inspect.signature(constructor).parameters["size"]

    assert parameter.default is inspect.Parameter.empty
