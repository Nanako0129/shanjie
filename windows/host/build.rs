// An exe exports nothing by default. The marker symbols (main.rs) are exported on purpose so CI can tell the builds apart with
// `dumpbin /exports`: `release-ids` gives `SjMarkReleaseIds`, `timing` gives `SjMarkTiming`. `/EXPORT` also keeps the symbol from being dropped.
fn main() {
    let msvc = std::env::var("CARGO_CFG_TARGET_OS").is_ok_and(|v| v == "windows") && std::env::var("CARGO_CFG_TARGET_ENV").is_ok_and(|v| v == "msvc");
    if msvc {
        for (feature, symbol) in [("CARGO_FEATURE_RELEASE_IDS", "SjMarkReleaseIds"), ("CARGO_FEATURE_TIMING", "SjMarkTiming")] {
            if std::env::var_os(feature).is_some() {
                println!("cargo:rustc-link-arg-bins=/EXPORT:{symbol}");
            }
        }
    }
    println!("cargo:rerun-if-changed=build.rs");
}
