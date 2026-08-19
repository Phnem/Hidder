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
      recordTrace({
        transport: 'webhid',
        method: 'sendReport',
        report_id: reportId,
        bytes_hex: hex,
        byte_length: byteLength,
        stack: new Error().stack
      });
    }

    async sendFeatureReport(reportId, data) {
      const hex = bufferToHex(data);
      const byteLength = data ? (data.byteLength || data.length || 0) : 0;
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
      const key = `feature_${reportId}`;
      const hex = window.__protocolMinerCannedResponses[key] || window.__protocolMinerCannedResponses[reportId] || null;
      let dataView;
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

  const fakeHID = {
    async getDevices() {
      recordTrace({ transport: 'webhid', method: 'getDevices' });
      return fakeHIDDevices;
    },
    async requestDevice(options) {
      recordTrace({ transport: 'webhid', method: 'requestDevice', options: options });
      return fakeHIDDevices;
    },
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => true
  };

  const fakeUSB = {
    async getDevices() {
      recordTrace({ transport: 'webusb', method: 'getDevices' });
      return fakeUSBDevices;
    },
    async requestDevice(options) {
      recordTrace({ transport: 'webusb', method: 'requestDevice', options: options });
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
