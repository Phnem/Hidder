from miner.cli import _parser


def test_cli_declares_required_static_workflow_commands() -> None:
    subparser_action = next(action for action in _parser()._actions if action.dest == "command")
    for command in ("doctor", "ingest", "ingest-url", "ingest-all", "analyze", "report", "export", "clean-workspace"):
        assert command in subparser_action.choices
