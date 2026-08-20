# Security and Privacy Architecture — Hidder

Hidder (Peripheral Research Probe) is an open-source, non-invasive protocol observation tool designed to decode proprietary USB HID and WebHID communication protocols of gaming keyboards and mice.

---

## 1. Safety & Architecture Principles

### Zero Hardware Writes
* Hidder contains **no functions** to transmit raw HID writes, send arbitrary feature reports, or modify device firmware.
* Official vendor software (e.g. AULA WebHub, Armoury Crate, Bloody, Keychron Launcher) performs the configuration changes.
* Hidder is purely a passive **observer**.

### Physical Separation of Modes
* **WebHID Mode (Default for Web Configurator)**:
  * Uses Chrome DevTools Protocol (CDP) to instrument `navigator.hid` inside an isolated browser session with a clean temporary profile.
  * **Zero remote process APIs**: Does NOT call `OpenProcess`, `VirtualAllocEx`, `WriteProcessMemory`, or `CreateRemoteThread`.
  * Does NOT load or extract any native hook helper DLL.
* **Native Desktop Mode (For Installed Desktop Apps)**:
  * Attaches exclusively to **one** user-selected vendor application process (e.g., `AULA.exe`).
  * Explicit user consent prompt is displayed before attaching.
  * Uses standard user-mode Win32 API hooking (`WriteFile`, `HidD_SetFeature`, `HidD_GetFeature`, `HidD_SetOutputReport`, `HidD_GetInputReport`) via MinHook to record HID reports over a local Named Pipe.
  * Helper DLL (`Hidder.NativeObserver.x64.dll`) is stored in `%LOCALAPPDATA%\Hidder\Runtime\v0.3.0\` with full PE version metadata.
  * Completely detaches and releases all handles when the test session ends.

### Zero Persistence & System Cleanliness
* **No Kernel Drivers**: Operates entirely in Win32 user-mode.
* **No Background Services**: No Windows services are installed.
* **No Registry Startup**: No registry autoruns or startup keys are created.
* **No Network Payloads**: Does not download executable binaries or execute remote shellcode.
* **No Anti-AV Evasion**: Does not employ obfuscation, runtime API hashing, syscalls, process hollowing, AMSI bypassing, or signature spoofing.

---

## 2. Privacy & Anti-Keylogger Protection

* **Personal Data Scrubbing**: Automatically sanitizes user paths, Windows usernames (`%USERNAME%`), local IP addresses, and email addresses from exported bundles.
* **Anti-Keylogger Filtering**: Standard 8-byte boot keyboard reports (`00 00 04 ...`) outside guided analog baseline tests are dropped immediately and never stored in memory or JSON.
* **No USB Serial Numbers**: Serial numbers and unique hardware identifiers are omitted to protect user privacy.

---

## 3. Verifying Releases

Every official release includes:
1. Full source code on GitHub: `https://github.com/Phnem/Hidder`
2. Exact SHA-256 cryptographic hashes for all executables and runtime helpers.
3. Automated GitHub Actions CI build logs demonstrating build reproducibility.

### Reporting Security Issues
If you discover a security vulnerability or unexpected behavior, please report it via GitHub Issues or contact the maintainer directly at:
* **Telegram**: [https://t.me/Phnem_pro](https://t.me/Phnem_pro)
