// AskKeep's TaskDialogIndirect exists only in comctl32 version 6, which a process gets through an activation context. The DLL carries its own
// manifest as resource 2 (ISOLATIONAWARE_MANIFEST_RESOURCE_ID, the id a DLL's activation context is created from) asking for that version;
// AskKeep activates it around the call. Same linker arguments as the installer measurement's C++ probe (`/MANIFEST:EMBED,ID=2` and
// `/manifestdependency`), given to link.exe through cargo.
fn main() {
    let msvc = std::env::var("CARGO_CFG_TARGET_OS").is_ok_and(|v| v == "windows") && std::env::var("CARGO_CFG_TARGET_ENV").is_ok_and(|v| v == "msvc");
    if msvc {
        println!("cargo:rustc-cdylib-link-arg=/MANIFEST:EMBED,ID=2");
        println!(
            "cargo:rustc-cdylib-link-arg=/MANIFESTDEPENDENCY:type='win32' name='Microsoft.Windows.Common-Controls' version='6.0.0.0' processorArchitecture='*' publicKeyToken='6595b64144ccf1df' language='*'"
        );
    }
    println!("cargo:rerun-if-changed=build.rs");
}
