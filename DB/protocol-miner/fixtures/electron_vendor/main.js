const hid = require("node-hid");
function setDpi(value) { const packet = new Uint8Array(3); packet[0] = 0x21; packet[1] = value; device.sendFeatureReport(7, packet); }
