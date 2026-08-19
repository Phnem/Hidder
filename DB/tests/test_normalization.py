import pytest
from ingest.normalize.identifiers import normalize_vid_pid, extract_vid_pid_from_text, parse_hex_or_dec
from ingest.normalize.models import normalize_product_name, detect_category
from ingest.normalize.dedupe import evaluate_product_match


def test_vid_pid_normalization():
    # Test hex strings
    res = normalize_vid_pid("0x372E", "0x103E")
    assert res is not None
    assert res.vid == 0x372E
    assert res.pid == 0x103E
    assert res.vid_hex == "0x372E"
    assert res.pid_hex == "0x103E"

    # Test integers
    res_int = normalize_vid_pid(14126, 4158)
    assert res_int is not None
    assert res_int.vid == 0x372E
    assert res_int.pid == 0x103E

    # Test invalid values (VID 0)
    assert normalize_vid_pid(0, 100) is None
    assert normalize_vid_pid("invalid", "0x1000") is None


def test_extract_vid_pid_from_text():
    sample_text = """
    Found device USB\\VID_372E&PID_103E in driver catalog.
    Also HID\\VID_046D&PID_C539 with high polling rate.
    Code snippet: const filter = { vendorId: 0x3434, productId: 0x0120 };
    """
    matches = extract_vid_pid_from_text(sample_text)
    pairs = [(m.vid_hex, m.pid_hex) for m in matches]

    assert ("0x372E", "0x103E") in pairs
    assert ("0x046D", "0xC539") in pairs
    assert ("0x3434", "0x0120") in pairs


def test_product_name_normalization():
    assert normalize_product_name("AULA", "AULA HERO 84 HE Mechanical Keyboard Wireless") == "HERO 84 HE"
    assert normalize_product_name("ATK", "ATK Blazing Sky F1 Wireless Mouse [2024]") == "Blazing Sky F1"
    assert normalize_product_name("Keychron", "Keychron Q1 Max Custom Keyboard (ANSI)") == "Q1 Max"


def test_category_detection():
    assert detect_category("AULA F75 Tri-Mode Gasket") == "keyboard"
    assert detect_category("ATK Blazing Sky F1 Wireless PAW3950") == "mouse"
    assert detect_category("Keychron M3 4K Polling Rate") == "mouse"


def test_dedupe_logic():
    # Exact URL match
    match = evaluate_product_match(
        "AULA", "Hero 84 HE", "https://aula.com/hero84",
        "AULA", "Hero 84 HE", "https://aula.com/hero84"
    )
    assert match.is_match is True

    # Same normalized name
    match2 = evaluate_product_match(
        "AULA", "HERO 84 HE", None,
        "AULA", "hero-84-he", None
    )
    assert match2.is_match is True

    # Different vendors
    match3 = evaluate_product_match(
        "AULA", "Hero 84", None,
        "Keychron", "Hero 84", None
    )
    assert match3.is_match is False
