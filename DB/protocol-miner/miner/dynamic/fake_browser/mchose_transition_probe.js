// TICKET-25 point B: observation-only probe for the M HUB Web device->configurator
// transition.
//
// This file OBSERVES. It never supplies a value the vendor code did not already
// compute, never suppresses an error, and never short-circuits a check. Every
// wrapper calls through and records; if the wrapped call throws, the throw is
// recorded and re-thrown unchanged. That property is what makes the resulting
// trace admissible as evidence about the app rather than about the harness.
//
// The static path it is instrumented against (from the bundle, TICKET-25 A):
//
//   requestDevice -> getDeviceMap() -> navigator.deviceMap
//                 -> gM.renewDevicesList(czDevices)
//                 -> setupDeviceSDK(dev)      [needs window.loadCZSharedData]
//                 -> clampDevices([dev])      [rejects with "device not allow"]
//                 -> createDeviceSDK(dev)
//                 -> device runtime -> updateDeviceState()  [SingletonDeviceStore]
//                 -> list item gains connectMode
//                 -> toDeviceAliveStatus(item).isDisabled === false
//                 -> row click -> D3() -> localStorage deviceName/lastKBProductKey
//                 -> router.push({name:"Keyboard"})
//
// Each arrow above has a tripwire below, so a run answers WHICH arrow failed
// instead of "the configurator did not open".
(function () {
  'use strict';
  if (window.__mcProbeInstalled) return;
  window.__mcProbeInstalled = true;

  var t0 = Date.now();
  var trace = [];
  window.__mcTrace = trace;

  function rec(stage, data) {
    try {
      trace.push({ t: Date.now() - t0, stage: stage, data: data === undefined ? null : data });
    } catch (e) { /* a probe must never break the page */ }
  }
  window.__mcRec = rec;
  rec('probe:installed');

  function safeDesc(d) {
    if (!d || typeof d !== 'object') return String(d);
    return {
      vendorId: d.vendorId, productId: d.productId, productName: d.productName,
      opened: d.opened,
      collections: (d.collections || []).map(function (c) {
        return { usagePage: c.usagePage, usage: c.usage };
      })
    };
  }
  window.__mcSafeDesc = safeDesc;

  // --- 1. window.loadCZSharedData: the CZ SDK the keyboard runtime depends on.
  // Intercepted at ASSIGNMENT so we see whether it ever arrives, and wrapped so
  // that clampDevices -- the gate that rejects a device with "device not allow"
  // -- reports its verdict per device.
  var _czLoader = undefined;
  try {
    Object.defineProperty(window, 'loadCZSharedData', {
      configurable: true,
      get: function () { return _czLoader; },
      set: function (fn) {
        rec('cz:loader-assigned', { type: typeof fn });
        if (typeof fn !== 'function') { _czLoader = fn; return; }
        _czLoader = function () {
          rec('cz:loader-called');
          var out;
          try {
            out = fn.apply(this, arguments);
          } catch (e) {
            rec('cz:loader-threw', String(e));
            throw e;
          }
          return Promise.resolve(out).then(function (sdk) {
            rec('cz:loader-resolved', { keys: sdk ? Object.keys(sdk).length : null });
            return wrapSdk(sdk);
          }, function (e) {
            rec('cz:loader-rejected', String(e));
            throw e;
          });
        };
      }
    });
  } catch (e) {
    rec('cz:loader-hook-failed', String(e));
  }

  // Shallow copy with a few functions wrapped. A copy rather than mutation, so
  // the vendor's own object is left exactly as it was; the app destructures the
  // resolved value, so a copy is indistinguishable to it.
  function wrapSdk(sdk) {
    if (!sdk || typeof sdk !== 'object') return sdk;
    var out = {};
    for (var k in sdk) out[k] = sdk[k];

    if (typeof sdk.clampDevices === 'function') {
      out.clampDevices = function (list) {
        var r = sdk.clampDevices.apply(sdk, arguments);
        rec('cz:clampDevices', {
          input: (list || []).map(safeDesc),
          kept: (r || []).length,
          verdict: (r || []).length === (list || []).length ? 'ALL_KEPT' : 'REJECTED'
        });
        return r;
      };
    }
    ['getDeviceData', 'getDeviceName', 'getDeviceType', 'getDeviceIsWireless',
     'getDeviceGLBKeyboardKey', 'pickUsefulProductName', 'getDeviceFeatSupport',
     'getKeyboardModelConfig'].forEach(function (name) {
      if (typeof sdk[name] !== 'function') return;
      out[name] = function () {
        var args = Array.prototype.slice.call(arguments);
        var r;
        try {
          r = sdk[name].apply(sdk, arguments);
        } catch (e) {
          rec('cz:' + name + ':threw', { args: args.map(shallow), err: String(e) });
          throw e;
        }
        rec('cz:' + name, { args: args.map(shallow), result: shallow(r) });
        return r;
      };
    });
    if (typeof sdk.createDeviceSDK === 'function') {
      out.createDeviceSDK = function (dev) {
        rec('cz:createDeviceSDK:enter', safeDesc(dev));
        var r;
        try {
          r = sdk.createDeviceSDK.apply(sdk, arguments);
        } catch (e) {
          rec('cz:createDeviceSDK:threw', String(e));
          throw e;
        }
        return Promise.resolve(r).then(function (v) {
          rec('cz:createDeviceSDK:ok', { storageKey: v && v.storageKey });
          return v;
        }, function (e) {
          rec('cz:createDeviceSDK:rejected', String(e));
          throw e;
        });
      };
    }
    return out;
  }

  function shallow(v) {
    if (v === null || v === undefined) return v;
    var t = typeof v;
    if (t === 'string' || t === 'number' || t === 'boolean') return v;
    if (t === 'function') return '[fn]';
    if (Array.isArray(v)) return v.length > 8 ? '[array:' + v.length + ']' : v.map(shallow);
    try {
      var keys = Object.keys(v);
      if (keys.length > 14) return '[object:' + keys.length + ' keys]';
      var o = {};
      keys.forEach(function (k) {
        var x = v[k];
        o[k] = (x && typeof x === 'object') ? '[object]' : (typeof x === 'function' ? '[fn]' : x);
      });
      return o;
    } catch (e) { return '[unserialisable]'; }
  }

  // --- 2. localStorage tripwire. D3() writes deviceName + lastKBProductKey
  // immediately before router.push({name:"Keyboard"}). Seeing these two keys
  // written is proof the click handler reached the keyboard branch; not seeing
  // them, with a click delivered, is proof it did not.
  try {
    var ls = window.localStorage;
    var setItem = ls.setItem.bind(ls);
    Object.defineProperty(window.localStorage, 'setItem', {
      configurable: true,
      value: function (k, v) {
        if (k === 'deviceName' || k === 'lastKBProductKey' || k === 'lastMouseProductKey') {
          rec('ls:set', { key: k, value: String(v).slice(0, 80) });
        }
        return setItem(k, v);
      }
    });
  } catch (e) {
    rec('ls:hook-failed', String(e));
  }

  // --- 3. Navigation. The Keyboard route is a plain vue-router push, so both
  // history and the router's own current route are recorded; a push that is
  // issued and then reverted looks different from one never issued.
  ['pushState', 'replaceState'].forEach(function (m) {
    try {
      var orig = history[m].bind(history);
      history[m] = function () {
        rec('history:' + m, String(arguments[2]).slice(0, 120));
        return orig.apply(history, arguments);
      };
    } catch (e) { rec('history:hook-failed', String(e)); }
  });
  window.addEventListener('hashchange', function () { rec('hashchange', location.hash); });

  // --- 4. Failures that produce no console error. An async handler that
  // rejects silently is the exact shape of "click did nothing", so both are
  // recorded; absence of a console error is not evidence of success.
  window.addEventListener('error', function (e) {
    rec('window:error', { msg: String(e.message).slice(0, 200), src: String(e.filename).slice(0, 120), line: e.lineno });
  });
  window.addEventListener('unhandledrejection', function (e) {
    var r = e.reason;
    rec('window:unhandledrejection', String(r && r.stack ? r.stack : r).slice(0, 400));
  });
})();
