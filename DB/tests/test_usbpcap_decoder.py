from ingest.usbpcap_decoder import decode_usbpcap_frame


def test_control_set_report_matches_tshark_reference_frame_19() -> None:
    frame = bytes.fromhex("1c006086621a0dbdffff000000001b00000200020000024800000000210904020100400004010001" + "00" * 62)
    urb = decode_usbpcap_frame(frame)
    assert urb is not None
    assert (urb.transfer_type, urb.control_stage, urb.device_address, urb.endpoint, urb.direction) == ("control", "setup", 2, 0, "host_to_device")
    assert urb.setup == {"bmRequestType": 0x21, "bRequest": 0x09, "wValue": 0x0204, "wIndex": 1, "wLength": 64}
    assert len(urb.payload) == 64 and urb.payload[:4] == bytes.fromhex("04010001")


def test_interrupt_in_matches_tshark_reference_frame_20() -> None:
    frame = bytes.fromhex("1b00200abd080dbdffff000000000900010200020082014000000004010001" + "00" * 60)
    urb = decode_usbpcap_frame(frame)
    assert urb is not None
    assert (urb.transfer_type, urb.control_stage, urb.device_address, urb.endpoint, urb.endpoint_number, urb.direction) == ("interrupt", None, 2, 0x82, 2, "device_to_host")
    assert urb.payload == bytes.fromhex("04010001") + bytes(60)


def test_interrupt_out_uses_tshark_irp_direction_not_endpoint_guess() -> None:
    # Gravastar V75 frame 5153: tshark reports interrupt OUT at endpoint 0x09.
    frame = bytes.fromhex("1b00b077cd280fd3ffff00000000090000010018000901400000005c06019701020304" + "00" * 56)
    urb = decode_usbpcap_frame(frame)
    assert urb is not None
    assert (urb.transfer_type, urb.endpoint, urb.direction, len(urb.payload)) == ("interrupt", 0x09, "host_to_device", 64)
