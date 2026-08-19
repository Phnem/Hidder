from miner.cli import _parser
from miner.cli import main


def test_cli_declares_required_static_workflow_commands() -> None:
    subparser_action = next(action for action in _parser()._actions if action.dest == "command")
    for command in ("doctor", "ingest", "ingest-url", "ingest-all", "ingest-cas", "analyze", "report", "export", "clean-workspace"):
        assert command in subparser_action.choices


def test_no_network_rejects_url_ingest() -> None:
    assert main(["--no-network", "ingest-url", "https://example.invalid/utility.zip"]) == 2


def test_doctor_accepts_machine_readable_output() -> None:
    assert main(["--json", "doctor"]) == 0
