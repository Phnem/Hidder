/**
 * Peripheral Protocol Miner - Fake WebHID & WebUSB Injected Runtime
 * Injected into Chromium before any application code executes.
 * Captures all vendor transport interactions into immutable trace events without physical device access.
 */
(function () {
  'use strict';

  if (window.__protocolMinerRuntimeInjected) {
    return;
  }
  window.__protocolMinerRuntimeInjected = true;

  window.__protocolMinerCurrentActionId = window.__protocolMinerCurrentActionId || null;
  window.__protocolMinerCurrentSemanticContext = window.__protocolMinerCurrentSemanticContext || null;
  window.__protocolMinerCannedResponses = window.__protocolMinerCannedResponses || {};
  window.__protocolMinerRecordedTraces = window.__protocolMinerRecordedTraces || [];

  // response_delivery_mode: how a bridge-produced reply to a HOST_TO_DEVICE
  // sendReport is delivered back to the page. Currently the only supported
  // mode - kept as a named constant (not a magic string scattered around)
  // so a future second mode (e.g. synchronous GET-style feature reports)
  // has an obvious place to branch on.
  const RESPONSE_DELIVERY_MODE_ASYNC_INPUTREPORT = 'ASYNC_INPUTREPORT';

  // response_latency_ms: simulated device->host reply latency, profile/
  // config-driven (window.__protocolMinerResponseLatencyMs, or a per-device
  // descriptor.responseLatencyMs - see FakeHIDDevice constructor), NOT a
  // hardcoded per-vendor constant. Falls back to this generic default.
  //
  // Why any delay at all: vendor protocols that pair a sendReport with an
  // async inputreport commonly do "send, then clear any stale queued
  // replies, then wait for the fresh one" on the read side (observed
  // directly in one real vendor's own bundle: a clear-receive-queue helper
  // does an unconditional queue-wipe right after sendReport, before the
  // actual wait/poll starts - this is a generic pattern, not unique to that
  // vendor). A same-microtask reply can win that race and land in the queue
  // *before* the clear runs, so the app wipes out its own correct,
  // freshly-arrived reply and then times out waiting for one that will never
  // come again. Real hardware never triggers this because a real reply
  // always arrives after genuine I/O latency, well after any such immediate
  // synchronous continuation. A small nonzero macrotask delay (setTimeout,
  // not Promise.resolve().then which is a same-tick microtask) reproduces
  // that ordering generically, for any protocol with this pattern.
  const DEFAULT_RESPONSE_LATENCY_MS = 8;

  function recordTrace(event) {
    const trace = {
      timestamp: new Date().toISOString(),
      url: window.location.href,
      ui_action_id: window.__protocolMinerCurrentActionId,
      semantic_context: window.__protocolMinerCurrentSemanticContext,
      ...event
    };
    window.__protocolMinerRecordedTraces.push(trace);
    if (typeof window.__protocolMinerBridgeRecord === 'function') {
      try {
        window.__protocolMinerBridgeRecord(JSON.stringify(trace));
      } catch (e) {}
    }
    // Also emit to console for fallback capture
    try {
      console.info('__PM_TRACE__:' + JSON.stringify(trace));
    } catch (e) {}
  }

  function bufferToHex(buffer) {
    if (!buffer) return null;
    let bytes;
    if (buffer instanceof ArrayBuffer) {
      bytes = new Uint8Array(buffer);
    } else if (ArrayBuffer.isView(buffer)) {
      bytes = new Uint8Array(buffer.buffer, buffer.byteOffset, buffer.byteLength);
    } else if (Array.isArray(buffer)) {
      bytes = new Uint8Array(buffer);
    } else {
      return null;
    }
    let hex = '';
    for (let i = 0; i < bytes.length; i++) {
      hex += bytes[i].toString(16).padStart(2, '0');
    }
    return hex;
  }

  function hexToBytes(hex) {
    if (!hex) return new Uint8Array(0);
    const cleanHex = hex.replace(/[\s-]/g, '');
    const len = Math.floor(cleanHex.length / 2);
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) {
      bytes[i] = parseInt(cleanHex.substring(i * 2, i * 2 + 2), 16) || 0;
    }
    return bytes;
  }

  // --- Fake HID Device ---
  class FakeHIDDevice extends EventTarget {
    constructor(descriptor) {
      super();
      this.vendorId = descriptor.vendorId || 0x1234;
      this.productId = descriptor.productId || 0x5678;
      this.productName = descriptor.productName || 'Simulated Protocol Miner HID Device';
      this.opened = false;
      // Precedence: per-device descriptor override > global page-level
      // override (window.__protocolMinerResponseLatencyMs, settable by any
      // profile/config builder before this script runs) > generic default.
      this.responseLatencyMs = descriptor.responseLatencyMs != null
        ? descriptor.responseLatencyMs
        : (window.__protocolMinerResponseLatencyMs != null
          ? window.__protocolMinerResponseLatencyMs
          : DEFAULT_RESPONSE_LATENCY_MS);
      this.responseDeliveryMode = descriptor.responseDeliveryMode || RESPONSE_DELIVERY_MODE_ASYNC_INPUTREPORT;
      this.collections = descriptor.collections || [
        {
          usagePage: 0xff00,
          usage: 0x01,
          inputReports: [{ reportId: 0, items: [] }],
          outputReports: [{ reportId: 0, items: [] }],
          featureReports: [{ reportId: 0, items: [] }]
        }
      ];
    }

    async open() {
      this.opened = true;
      recordTrace({
        transport: 'webhid',
        method: 'open',
        vendorId: this.vendorId,
        productId: this.productId,
        productName: this.productName
      });
    }

    async close() {
      this.opened = false;
      recordTrace({
        transport: 'webhid',
        method: 'close',
        vendorId: this.vendorId,
        productId: this.productId
      });
    }

    async sendReport(reportId, data) {
      const hex = bufferToHex(data);
      const byteLength = data ? (data.byteLength || data.length || 0) : 0;
      let bridgeResult = null;
      if (typeof window.__protocolMinerBridgeRespond === 'function') {
        try {
          bridgeResult = await window.__protocolMinerBridgeRespond({
            method: 'sendReport', report_id: reportId, bytes_hex: hex,
            vendorId: this.vendorId, productId: this.productId
          });
        } catch (e) { bridgeResult = null; }
      }
      recordTrace({
        transport: 'webhid',
        method: 'sendReport',
        report_id: reportId,
        bytes_hex: hex,
        byte_length: byteLength,
        stack: new Error().stack
      });
      // Many vendor devices reply to an OUTPUT report on report_id N with an
      // asynchronous INPUT report on the same report_id, delivered via the
      // 'inputreport' event - not via sendReport's return value (which is
      // void per spec). If the response engine produced a reply, deliver it
      // that way, after responseLatencyMs (see its definition above for why
      // a same-microtask delivery is wrong - RESPONSE_DELIVERY_MODE_ASYNC_INPUTREPORT).
      if (bridgeResult && bridgeResult.reply && typeof bridgeResult.reply.hex === 'string') {
        const replyReportId = bridgeResult.reply.reportId != null ? bridgeResult.reply.reportId : reportId;
        setTimeout(() => this.simulateInputReport(replyReportId, bridgeResult.reply.hex), this.responseLatencyMs);
      }
    }

    async sendFeatureReport(reportId, data) {
      const hex = bufferToHex(data);
      const byteLength = data ? (data.byteLength || data.length || 0) : 0;
      if (typeof window.__protocolMinerBridgeRespond === 'function') {
        try {
          await window.__protocolMinerBridgeRespond({
            method: 'sendFeatureReport', report_id: reportId, bytes_hex: hex,
            vendorId: this.vendorId, productId: this.productId
          });
        } catch (e) {}
      }
      recordTrace({
        transport: 'webhid',
        method: 'sendFeatureReport',
        report_id: reportId,
        bytes_hex: hex,
        byte_length: byteLength,
        stack: new Error().stack
      });
    }

    async receiveFeatureReport(reportId) {
      let dataView;
      // Preferred path: a live Python-side response engine (adaptive response
      // ladder, stateful). Falls back to static canned_responses / zero-fill
      // below when no bridge is registered - this keeps existing sync tests
      // (which only set canned_responses) working unchanged.
      if (typeof window.__protocolMinerBridgeRespond === 'function') {
        let bridgeResult = null;
        try {
          bridgeResult = await window.__protocolMinerBridgeRespond({
            method: 'receiveFeatureReport', report_id: reportId,
            vendorId: this.vendorId, productId: this.productId
          });
        } catch (e) { bridgeResult = null; }
        if (bridgeResult && typeof bridgeResult.hex === 'string') {
          const bytes = hexToBytes(bridgeResult.hex);
          dataView = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
          recordTrace({
            transport: 'webhid',
            method: 'receiveFeatureReport',
            report_id: reportId,
            bytes_hex: bufferToHex(dataView),
            byte_length: dataView.byteLength,
            strategy: bridgeResult.strategy,
            confidence: bridgeResult.confidence
          });
          return dataView;
        }
      }
      const key = `feature_${reportId}`;
      const hex = window.__protocolMinerCannedResponses[key] || window.__protocolMinerCannedResponses[reportId] || null;
      if (hex) {
        const bytes = hexToBytes(hex);
        dataView = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
      } else {
        recordTrace({
          transport: 'webhid',
          method: 'receiveFeatureReport_unknown',
          report_id: reportId,
          note: 'unresolved_canned_response'
        });
        const defaultBytes = new Uint8Array(64);
        dataView = new DataView(defaultBytes.buffer);
      }
      recordTrace({
        transport: 'webhid',
        method: 'receiveFeatureReport',
        report_id: reportId,
        bytes_hex: bufferToHex(dataView),
        byte_length: dataView.byteLength
      });
      return dataView;
    }

    simulateInputReport(reportId, data) {
      const bytes = typeof data === 'string' ? hexToBytes(data) : (data instanceof Uint8Array ? data : new Uint8Array(data));
      const dataView = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
      const event = new CustomEvent('inputreport', {
        detail: {
          reportId: reportId,
          data: dataView,
          device: this
        }
      });
      Object.defineProperty(event, 'reportId', { value: reportId });
      Object.defineProperty(event, 'data', { value: dataView });
      Object.defineProperty(event, 'device', { value: this });
      this.dispatchEvent(event);
      if (typeof this.oninputreport === 'function') {
        this.oninputreport(event);
      }
      recordTrace({
        transport: 'webhid',
        method: 'simulateInputReport',
        report_id: reportId,
        bytes_hex: bufferToHex(dataView),
        byte_length: dataView.byteLength
      });
    }
  }

  // --- Fake USB Device ---
  class FakeUSBDevice extends EventTarget {
    constructor(descriptor) {
      super();
      this.vendorId = descriptor.vendorId || 0x1234;
      this.productId = descriptor.productId || 0x5678;
      this.productName = descriptor.productName || 'Simulated Protocol Miner USB Device';
      this.opened = false;
      this.configurations = descriptor.configurations || [];
      this.configuration = descriptor.configuration || null;
    }

    async open() {
      this.opened = true;
      recordTrace({
        transport: 'webusb',
        method: 'open',
        vendorId: this.vendorId,
        productId: this.productId
      });
    }

    async close() {
      this.opened = false;
      recordTrace({
        transport: 'webusb',
        method: 'close',
        vendorId: this.vendorId,
        productId: this.productId
      });
    }

    async selectConfiguration(configNumber) {
      recordTrace({
        transport: 'webusb',
        method: 'selectConfiguration',
        configNumber: configNumber
      });
    }

    async claimInterface(interfaceNumber) {
      recordTrace({
        transport: 'webusb',
        method: 'claimInterface',
        interfaceNumber: interfaceNumber
      });
    }

    async releaseInterface(interfaceNumber) {
      recordTrace({
        transport: 'webusb',
        method: 'releaseInterface',
        interfaceNumber: interfaceNumber
      });
    }

    async controlTransferOut(setup, data) {
      const hex = bufferToHex(data);
      recordTrace({
        transport: 'webusb',
        method: 'controlTransferOut',
        setup: setup,
        bytes_hex: hex,
        byte_length: data ? (data.byteLength || data.length || 0) : 0
      });
      return { status: 'ok', bytesWritten: data ? data.byteLength : 0 };
    }

    async controlTransferIn(setup, length) {
      const key = `usb_ctrl_${setup.request}_${setup.value}`;
      const hex = window.__protocolMinerCannedResponses[key] || null;
      const bytes = hex ? hexToBytes(hex) : new Uint8Array(length || 64);
      const dataView = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
      recordTrace({
        transport: 'webusb',
        method: 'controlTransferIn',
        setup: setup,
        bytes_hex: bufferToHex(dataView),
        byte_length: dataView.byteLength
      });
      return { status: 'ok', data: dataView };
    }

    async transferOut(endpointNumber, data) {
      const hex = bufferToHex(data);
      recordTrace({
        transport: 'webusb',
        method: 'transferOut',
        endpointNumber: endpointNumber,
        bytes_hex: hex,
        byte_length: data ? (data.byteLength || data.length || 0) : 0
      });
      return { status: 'ok', bytesWritten: data ? data.byteLength : 0 };
    }

    async transferIn(endpointNumber, length) {
      const key = `usb_ep_${endpointNumber}`;
      const hex = window.__protocolMinerCannedResponses[key] || null;
      const bytes = hex ? hexToBytes(hex) : new Uint8Array(length || 64);
      const dataView = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
      recordTrace({
        transport: 'webusb',
        method: 'transferIn',
        endpointNumber: endpointNumber,
        bytes_hex: bufferToHex(dataView),
        byte_length: dataView.byteLength
      });
      return { status: 'ok', data: dataView };
    }
  }

  // --- Fake Navigator Surfaces ---
  const fakeHIDDevices = [
    new FakeHIDDevice(window.__protocolMinerDeviceConfig || {})
  ];

  const fakeUSBDevices = [
    new FakeUSBDevice(window.__protocolMinerDeviceConfig || {})
  ];

  // Real WebHID/WebUSB only return a device from getDevices() *after* a
  // requestDevice() grant - a fresh page load with no prior grant sees an
  // empty list. Returning the device unconditionally (as this used to do)
  // makes every site think it already has a granted device on first mount,
  // which for at least one real vendor site (AULA's hero.aulastar.com)
  // triggers its own auto-reconnect UI path instead of the normal "click
  // Connect" flow - the same buggy reconnect path already characterized as
  // unreliable on real hardware's cached-grant reuse. Track grant state
  // properly instead, so the fake device behaves like a fresh pairing by
  // default (the one flow already proven reliable), matching real WebHID
  // semantics generically for any site, not just this one.
  let hidGranted = false;
  let usbGranted = false;

  const fakeHID = {
    // Marker so a harness can PROVE, on the live page, that the object the
    // vendor's code is about to write frames into is this one and not the real
    // WebHID stack. Without it a safety claim about "no real device is
    // reachable" rests on injection order having worked, which is an assumption
    // rather than a check -- and the failure mode is writing a frame built for a
    // factory reset into whatever keyboard happens to be plugged in.
    // Additive: nothing in the runtime reads it.
    __protocolMinerFake: true,
    async getDevices() {
      recordTrace({ transport: 'webhid', method: 'getDevices' });
      return hidGranted ? fakeHIDDevices : [];
    },
    async requestDevice(options) {
      recordTrace({ transport: 'webhid', method: 'requestDevice', options: options });
      hidGranted = true;
      return fakeHIDDevices;
    },
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => true
  };

  const fakeUSB = {
    async getDevices() {
      recordTrace({ transport: 'webusb', method: 'getDevices' });
      return usbGranted ? fakeUSBDevices : [];
    },
    async requestDevice(options) {
      recordTrace({ transport: 'webusb', method: 'requestDevice', options: options });
      usbGranted = true;
      return fakeUSBDevices[0];
    },
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => true
  };

  try {
    Object.defineProperty(navigator, 'hid', {
      value: fakeHID,
      writable: false,
      configurable: true
    });
  } catch (e) {
    navigator.hid = fakeHID;
  }

  try {
    Object.defineProperty(navigator, 'usb', {
      value: fakeUSB,
      writable: false,
      configurable: true
    });
  } catch (e) {
    navigator.usb = fakeUSB;
  }

  window.__protocolMinerDevices = {
    hid: fakeHIDDevices,
    usb: fakeUSBDevices
  };
})();
