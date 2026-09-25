"""EM-236: the schedule template and the systemd unit say what the runbook says they say."""

from __future__ import annotations

import configparser
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / "infra" / "quote-recording-schedule.yaml"
UNIT = ROOT / "infra" / "systemd" / "emporos-quotes.service"
INSTANCE = "i-0cec4ddd7cdd5f96d"


class _Cfn(yaml.SafeLoader):
    """A loader that keeps CloudFormation's short tags (!Sub, !Ref, !GetAtt) as plain values."""


def _tag(loader: _Cfn, suffix: str, node: yaml.Node) -> Any:
    if isinstance(node, yaml.ScalarNode):
        return {suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {suffix: loader.construct_sequence(node)}
    return {suffix: loader.construct_mapping(node)}  # type: ignore[arg-type]


_Cfn.add_multi_constructor("!", _tag)


def template() -> dict[str, Any]:
    return yaml.load(TEMPLATE.read_text(), Loader=_Cfn)


def unit() -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str  # type: ignore[assignment,method-assign]
    parser.read_string(UNIT.read_text())
    return parser


class TestSchedule:
    def test_it_starts_at_0840_and_stops_at_1550_kolkata_on_weekdays(self) -> None:
        resources = template()["Resources"]
        start, stop = (
            resources["StartSchedule"]["Properties"],
            resources["StopSchedule"]["Properties"],
        )

        assert start["ScheduleExpression"] == "cron(40 8 ? * MON-FRI *)"
        assert stop["ScheduleExpression"] == "cron(50 15 ? * MON-FRI *)"
        assert (
            start["ScheduleExpressionTimezone"]
            == stop["ScheduleExpressionTimezone"]
            == "Asia/Kolkata"
        )
        assert start["Target"]["Arn"].endswith(":ec2:startInstances")
        assert stop["Target"]["Arn"].endswith(":ec2:stopInstances")

    def test_the_role_can_only_start_and_stop_the_one_instance(self) -> None:
        template_ = template()
        role = template_["Resources"]["SchedulerRole"]["Properties"]
        (statement,) = role["Policies"][0]["PolicyDocument"]["Statement"]

        assert sorted(statement["Action"]) == ["ec2:StartInstances", "ec2:StopInstances"]
        assert statement["Resource"]["Sub"].endswith(":instance/${InstanceId}")
        assert template_["Parameters"]["InstanceId"]["Default"] == INSTANCE
        assert len(role["Policies"]) == 1 and "ManagedPolicyArns" not in role

    def test_only_the_scheduler_service_in_this_account_can_assume_the_role(self) -> None:
        trust = template()["Resources"]["SchedulerRole"]["Properties"]["AssumeRolePolicyDocument"]
        (statement,) = trust["Statement"]

        assert statement["Principal"] == {"Service": "scheduler.amazonaws.com"}
        assert "aws:SourceAccount" in statement["Condition"]["StringEquals"]

    def test_the_template_creates_nothing_else(self) -> None:
        kinds = {r["Type"] for r in template()["Resources"].values()}

        assert kinds == {"AWS::IAM::Role", "AWS::Scheduler::Schedule"}

    def test_both_schedules_can_be_paused_with_one_parameter(self) -> None:
        resources = template()["Resources"]

        for name in ("StartSchedule", "StopSchedule"):
            assert resources[name]["Properties"]["State"] == {"Ref": "State"}


class TestUnit:
    def test_it_runs_the_quotes_only_command_and_nothing_else(self) -> None:
        service = unit()["Service"]

        assert service["ExecStart"].endswith("emporos worker record-quotes")
        assert (
            " worker run" not in service["ExecStart"] and " worker live" not in service["ExecStart"]
        )
        assert service["EnvironmentFile"] == "/run/emporos/env"

    def test_it_needs_the_env_unit_and_cannot_pull_in_the_worker(self) -> None:
        parser = unit()
        text = UNIT.read_text()

        assert "emporos-env.service" in parser["Unit"]["Requires"]
        for key in ("Requires", "Wants", "After", "Before", "BindsTo", "PartOf", "Upholds"):
            assert "emporos-worker" not in parser["Unit"].get(key, "")
        assert "Conflicts" not in parser["Unit"]  # starting this must never stop a live worker
        assert "emporos-worker.service" in parser["Service"]["ExecCondition"]  # only to skip
        assert "start emporos-worker" not in text and "systemctl start" not in text

    def test_it_is_enabled_at_boot_and_given_time_to_upload_on_a_stop(self) -> None:
        parser = unit()

        assert parser["Install"]["WantedBy"] == "multi-user.target"
        assert int(parser["Service"]["TimeoutStopSec"]) >= 60
        assert parser["Service"]["Restart"] == "on-failure"
