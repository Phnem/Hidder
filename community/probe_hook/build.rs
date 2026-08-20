fn main() {
    #[cfg(windows)]
    {
        let mut res = winres::WindowsResource::new();
        res.set("FileDescription", "Hidder Native HID Observer Hook");
        res.set("ProductName", "Hidder");
        res.set("CompanyName", "Phnem");
        res.set("OriginalFilename", "Hidder.NativeObserver.x64.dll");
        res.set("LegalCopyright", "Copyright (C) 2026 Phnem. MIT License.");
        let _ = res.compile();
    }
}
