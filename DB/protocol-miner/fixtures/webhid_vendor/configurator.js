navigator.hid.requestDevice({filters: [{vendorId: 0x372E, productId: 0x103E}]});
function setActuation(value) {
  const packet = new Uint8Array(3);
  packet[0] = 0x13;
  packet[1] = value * 100;
  device.sendReport(9, packet);
}
