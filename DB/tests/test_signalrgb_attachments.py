from ingest.signalrgb_attachments import discover_attachments


def test_relative_gitlab_upload_uses_numeric_project_route() -> None:
    issue = {
        "iid": 845,
        "id": 193154406,
        "project_id": 22875485,
        "web_url": "https://gitlab.com/signalrgb/signal-plugins/-/work_items/845",
        "description": "[capture.pcapng](/uploads/secret/capture.pcapng)",
    }

    attachments = discover_attachments([issue])

    assert attachments[0]["attachment_url"] == (
        "https://gitlab.com/-/project/22875485/uploads/secret/capture.pcapng"
    )


def test_absolute_gitlab_upload_is_preserved() -> None:
    url = "https://gitlab.com/-/project/22875485/uploads/secret/capture.pcapng"
    issue = {"description": f"[capture]({url})"}

    assert discover_attachments([issue])[0]["attachment_url"] == url
