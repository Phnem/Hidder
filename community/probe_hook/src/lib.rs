#![allow(non_camel_case_types, non_snake_case)]

use std::ffi::c_void;
use std::sync::atomic::{AtomicBool, AtomicPtr, Ordering};
use minhook::MinHook;

type HANDLE = *mut c_void;
type BOOL = i32;
type DWORD = u32;
type LPCVOID = *const c_void;
type LPDWORD = *mut u32;
type LPOVERLAPPED = *mut c_void;
type ULONG = u32;
type USHORT = u16;

#[repr(C)]
struct HIDD_ATTRIBUTES {
    size: ULONG,
    vendor_id: USHORT,
    product_id: USHORT,
    version_number: USHORT,
}

extern "system" {
    fn GetModuleHandleA(lpModuleName: *const i8) -> HANDLE;
    fn GetProcAddress(hModule: HANDLE, lpProcName: *const i8) -> *mut c_void;
    fn CreateFileA(
        lpFileName: *const i8,
        dwDesiredAccess: DWORD,
        dwShareMode: DWORD,
        lpSecurityAttributes: *mut c_void,
        dwCreationDisposition: DWORD,
        dwFlagsAndAttributes: DWORD,
        hTemplateFile: HANDLE,
    ) -> HANDLE;
    fn CloseHandle(hObject: HANDLE) -> BOOL;
    fn WriteFile(
        hFile: HANDLE,
        lpBuffer: LPCVOID,
        nNumberOfBytesToWrite: DWORD,
        lpNumberOfBytesWritten: LPDWORD,
        lpOverlapped: LPOVERLAPPED,
    ) -> BOOL;
}

// Function pointer types
type FnWriteFile = extern "system" fn(HANDLE, LPCVOID, DWORD, LPDWORD, LPOVERLAPPED) -> BOOL;
type FnHidD_SetFeature = extern "system" fn(HANDLE, *const c_void, ULONG) -> BOOL;
type FnHidD_GetFeature = extern "system" fn(HANDLE, *mut c_void, ULONG) -> BOOL;
type FnHidD_SetOutputReport = extern "system" fn(HANDLE, *const c_void, ULONG) -> BOOL;
type FnHidD_GetInputReport = extern "system" fn(HANDLE, *mut c_void, ULONG) -> BOOL;
type FnHidD_GetAttributes = extern "system" fn(HANDLE, *mut HIDD_ATTRIBUTES) -> BOOL;

static ORIGINAL_WRITE_FILE: AtomicPtr<c_void> = AtomicPtr::new(std::ptr::null_mut());
static ORIGINAL_SET_FEATURE: AtomicPtr<c_void> = AtomicPtr::new(std::ptr::null_mut());
static ORIGINAL_GET_FEATURE: AtomicPtr<c_void> = AtomicPtr::new(std::ptr::null_mut());
static ORIGINAL_SET_OUTPUT_REPORT: AtomicPtr<c_void> = AtomicPtr::new(std::ptr::null_mut());
static ORIGINAL_GET_INPUT_REPORT: AtomicPtr<c_void> = AtomicPtr::new(std::ptr::null_mut());
static FN_HID_GET_ATTRIBUTES: AtomicPtr<c_void> = AtomicPtr::new(std::ptr::null_mut());

static PIPE_HANDLE: AtomicPtr<c_void> = AtomicPtr::new(std::ptr::null_mut());
static IS_INITIALIZED: AtomicBool = AtomicBool::new(false);
static TARGET_VID: std::sync::atomic::AtomicU16 = std::sync::atomic::AtomicU16::new(0);
static TARGET_PID: std::sync::atomic::AtomicU16 = std::sync::atomic::AtomicU16::new(0);

const GENERIC_WRITE: DWORD = 0x40000000;
const OPEN_EXISTING: DWORD = 3;

fn send_event(api: &str, direction: &str, report_id: u8, buffer: &[u8], vid: u16, pid: u16) {
    let pipe = PIPE_HANDLE.load(Ordering::Relaxed);
    if pipe.is_null() {
        return;
    }

    let mut hex = String::with_capacity(buffer.len() * 2);
    for b in buffer {
        hex.push_str(&format!("{:02x}", b));
    }

    let json_msg = format!(
        r#"{{"api":"{}","direction":"{}","report_id":{},"length":{},"bytes_hex":"{}","vid":"0x{:04X}","pid":"0x{:04X}","timestamp":{:.3}}}"#,
        api,
        direction,
        report_id,
        buffer.len(),
        hex,
        vid,
        pid,
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs_f64()
    ) + "\n";

    let mut written: DWORD = 0;
    unsafe {
        WriteFile(
            pipe,
            json_msg.as_ptr() as LPCVOID,
            json_msg.len() as DWORD,
            &mut written,
            std::ptr::null_mut(),
        );
    }
}

fn check_hid_device(handle: HANDLE) -> Option<(u16, u16)> {
    let fn_attr = FN_HID_GET_ATTRIBUTES.load(Ordering::Relaxed);
    if fn_attr.is_null() {
        return None;
    }
    let get_attr: FnHidD_GetAttributes = unsafe { std::mem::transmute(fn_attr) };
    let mut attr = HIDD_ATTRIBUTES {
        size: std::mem::size_of::<HIDD_ATTRIBUTES>() as ULONG,
        vendor_id: 0,
        product_id: 0,
        version_number: 0,
    };
    if get_attr(handle, &mut attr) != 0 {
        let t_vid = TARGET_VID.load(Ordering::Relaxed);
        let t_pid = TARGET_PID.load(Ordering::Relaxed);
        if t_vid == 0 || (attr.vendor_id == t_vid && (t_pid == 0 || attr.product_id == t_pid)) {
            return Some((attr.vendor_id, attr.product_id));
        }
    }
    None
}

// Hooked WriteFile
extern "system" fn hooked_write_file(
    handle: HANDLE,
    buffer: LPCVOID,
    length: DWORD,
    written: LPDWORD,
    overlapped: LPOVERLAPPED,
) -> BOOL {
    let orig_ptr = ORIGINAL_WRITE_FILE.load(Ordering::Relaxed);
    let orig: FnWriteFile = unsafe { std::mem::transmute(orig_ptr) };

    if !buffer.is_null() && length > 0 {
        if let Some((vid, pid)) = check_hid_device(handle) {
            let slice = unsafe { std::slice::from_raw_parts(buffer as *const u8, length as usize) };
            let report_id = slice[0];
            send_event("WriteFile", "out", report_id, slice, vid, pid);
        }
    }

    orig(handle, buffer, length, written, overlapped)
}

// Hooked HidD_SetFeature
extern "system" fn hooked_set_feature(
    handle: HANDLE,
    buffer: *const c_void,
    length: ULONG,
) -> BOOL {
    let orig_ptr = ORIGINAL_SET_FEATURE.load(Ordering::Relaxed);
    let orig: FnHidD_SetFeature = unsafe { std::mem::transmute(orig_ptr) };

    if !buffer.is_null() && length > 0 {
        let (vid, pid) = check_hid_device(handle).unwrap_or((0, 0));
        let slice = unsafe { std::slice::from_raw_parts(buffer as *const u8, length as usize) };
        let report_id = slice[0];
        send_event("HidD_SetFeature", "feature_out", report_id, slice, vid, pid);
    }

    orig(handle, buffer, length)
}

// Hooked HidD_GetFeature
extern "system" fn hooked_get_feature(
    handle: HANDLE,
    buffer: *mut c_void,
    length: ULONG,
) -> BOOL {
    let orig_ptr = ORIGINAL_GET_FEATURE.load(Ordering::Relaxed);
    let orig: FnHidD_GetFeature = unsafe { std::mem::transmute(orig_ptr) };

    let res = orig(handle, buffer, length);
    if res != 0 && !buffer.is_null() && length > 0 {
        let (vid, pid) = check_hid_device(handle).unwrap_or((0, 0));
        let slice = unsafe { std::slice::from_raw_parts(buffer as *const u8, length as usize) };
        let report_id = slice[0];
        send_event("HidD_GetFeature", "feature_in", report_id, slice, vid, pid);
    }
    res
}

// Hooked HidD_SetOutputReport
extern "system" fn hooked_set_output_report(
    handle: HANDLE,
    buffer: *const c_void,
    length: ULONG,
) -> BOOL {
    let orig_ptr = ORIGINAL_SET_OUTPUT_REPORT.load(Ordering::Relaxed);
    let orig: FnHidD_SetOutputReport = unsafe { std::mem::transmute(orig_ptr) };

    if !buffer.is_null() && length > 0 {
        let (vid, pid) = check_hid_device(handle).unwrap_or((0, 0));
        let slice = unsafe { std::slice::from_raw_parts(buffer as *const u8, length as usize) };
        let report_id = slice[0];
        send_event("HidD_SetOutputReport", "out", report_id, slice, vid, pid);
    }

    orig(handle, buffer, length)
}

// Hooked HidD_GetInputReport
extern "system" fn hooked_get_input_report(
    handle: HANDLE,
    buffer: *mut c_void,
    length: ULONG,
) -> BOOL {
    let orig_ptr = ORIGINAL_GET_INPUT_REPORT.load(Ordering::Relaxed);
    let orig: FnHidD_GetInputReport = unsafe { std::mem::transmute(orig_ptr) };

    let res = orig(handle, buffer, length);
    if res != 0 && !buffer.is_null() && length > 0 {
        let (vid, pid) = check_hid_device(handle).unwrap_or((0, 0));
        let slice = unsafe { std::slice::from_raw_parts(buffer as *const u8, length as usize) };
        let report_id = slice[0];
        send_event("HidD_GetInputReport", "in", report_id, slice, vid, pid);
    }
    res
}

fn initialize_hooks() {
    if IS_INITIALIZED.swap(true, Ordering::SeqCst) {
        return;
    }

    // Connect to Named Pipe
    let pipe_name = b"\\\\.\\pipe\\PeripheralResearch_Observer\0";
    let h_pipe = unsafe {
        CreateFileA(
            pipe_name.as_ptr() as *const i8,
            GENERIC_WRITE,
            0,
            std::ptr::null_mut(),
            OPEN_EXISTING,
            0,
            std::ptr::null_mut(),
        )
    };
    if h_pipe as isize != -1 && !h_pipe.is_null() {
        PIPE_HANDLE.store(h_pipe, Ordering::Relaxed);
    }

    unsafe {
        // Resolve kernel32.dll / kernelbase.dll WriteFile
        let h_kernel32 = GetModuleHandleA(b"kernel32.dll\0".as_ptr() as *const i8);
        if !h_kernel32.is_null() {
            let p_write_file = GetProcAddress(h_kernel32, b"WriteFile\0".as_ptr() as *const i8);
            if !p_write_file.is_null() {
                if let Ok(orig) = MinHook::create_hook(p_write_file, hooked_write_file as *mut c_void) {
                    ORIGINAL_WRITE_FILE.store(orig, Ordering::SeqCst);
                }
            }
        }

        // Resolve hid.dll exports
        let h_hid = GetModuleHandleA(b"hid.dll\0".as_ptr() as *const i8);
        if !h_hid.is_null() {
            let p_get_attr = GetProcAddress(h_hid, b"HidD_GetAttributes\0".as_ptr() as *const i8);
            if !p_get_attr.is_null() {
                FN_HID_GET_ATTRIBUTES.store(p_get_attr, Ordering::SeqCst);
            }

            let p_set_feat = GetProcAddress(h_hid, b"HidD_SetFeature\0".as_ptr() as *const i8);
            if !p_set_feat.is_null() {
                if let Ok(orig) = MinHook::create_hook(p_set_feat, hooked_set_feature as *mut c_void) {
                    ORIGINAL_SET_FEATURE.store(orig, Ordering::SeqCst);
                }
            }

            let p_get_feat = GetProcAddress(h_hid, b"HidD_GetFeature\0".as_ptr() as *const i8);
            if !p_get_feat.is_null() {
                if let Ok(orig) = MinHook::create_hook(p_get_feat, hooked_get_feature as *mut c_void) {
                    ORIGINAL_GET_FEATURE.store(orig, Ordering::SeqCst);
                }
            }

            let p_set_out = GetProcAddress(h_hid, b"HidD_SetOutputReport\0".as_ptr() as *const i8);
            if !p_set_out.is_null() {
                if let Ok(orig) = MinHook::create_hook(p_set_out, hooked_set_output_report as *mut c_void) {
                    ORIGINAL_SET_OUTPUT_REPORT.store(orig, Ordering::SeqCst);
                }
            }

            let p_get_in = GetProcAddress(h_hid, b"HidD_GetInputReport\0".as_ptr() as *const i8);
            if !p_get_in.is_null() {
                if let Ok(orig) = MinHook::create_hook(p_get_in, hooked_get_input_report as *mut c_void) {
                    ORIGINAL_GET_INPUT_REPORT.store(orig, Ordering::SeqCst);
                }
            }
        }

        // Enable all created MinHook hooks
        let _ = MinHook::enable_all_hooks();
    }
}

#[no_mangle]
pub extern "system" fn DllMain(
    _hinst_dll: *mut c_void,
    fdw_reason: u32,
    _lpv_reserved: *mut c_void,
) -> i32 {
    const DLL_PROCESS_ATTACH: u32 = 1;
    const DLL_PROCESS_DETACH: u32 = 0;

    match fdw_reason {
        DLL_PROCESS_ATTACH => {
            std::thread::spawn(initialize_hooks);
        }
        DLL_PROCESS_DETACH => {
            let _ = unsafe { MinHook::disable_all_hooks() };
            let pipe = PIPE_HANDLE.swap(std::ptr::null_mut(), Ordering::SeqCst);
            if !pipe.is_null() {
                unsafe { CloseHandle(pipe); }
            }
        }
        _ => {}
    }
    1
}
