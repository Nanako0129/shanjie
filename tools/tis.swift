// Input-source helper for on-device experiments (docs/research-log.md 2026-10-05, secure input).
// Build: swiftc -O -o /tmp/tis tools/tis.swift. A new bundle ID is accepted by `register` but only
// becomes addable in System Settings after a log out; `enable` on it then reports noErr without effect.
// usage: tis register <app path> | enable <id> | disable <id> | show <id-prefix>
import Carbon
import Foundation
let a = CommandLine.arguments
func sources(_ id: String, all: Bool = true) -> [TISInputSource] {
    let props = [kTISPropertyInputSourceID as String: id] as CFDictionary
    return (TISCreateInputSourceList(props, all).takeRetainedValue() as? [TISInputSource]) ?? []
}
func prop(_ s: TISInputSource, _ k: CFString) -> Any? {
    guard let p = TISGetInputSourceProperty(s, k) else { return nil }
    return Unmanaged<AnyObject>.fromOpaque(p).takeUnretainedValue()
}
let usage = "usage: tis register <app path> | enable <id> | disable <id> | show <id-prefix>"
guard a.count == 3 else { print(usage); exit(2) }
switch a[1] {
case "register":
    print("register", TISRegisterInputSource(URL(fileURLWithPath: a[2]) as CFURL))
case "enable":
    for s in sources(a[2]) { print("enable", TISEnableInputSource(s)) }
case "disable":
    for s in sources(a[2]) { print("disable", TISDisableInputSource(s)) }
case "show":
    let all = (TISCreateInputSourceList(nil, true).takeRetainedValue() as? [TISInputSource]) ?? []
    for s in all {
        let id = prop(s, kTISPropertyInputSourceID) as? String ?? ""
        guard id.hasPrefix(a[2]) else { continue }
        print(id, "enabled=\(prop(s, kTISPropertyInputSourceIsEnabled) ?? "-")", "selectCapable=\(prop(s, kTISPropertyInputSourceIsSelectCapable) ?? "-")", "type=\(prop(s, kTISPropertyInputSourceType) ?? "-")")
    }
default: print(usage); exit(2)
}
