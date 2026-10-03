import AppKit
import Carbon
import InputMethodKit
import ShanjieKit

// docs/contracts/s3b.md sections 4, 5 and 9. Arguments: none (run as the input method), exactly
// `install`, or exactly `--selftest`. Anything else exits non-zero before touching anything.
// Output is fixed text only (R2): no paths, bundle IDs or input.

func say(_ message: StaticString) {
    FileHandle.standardError.write(Data("\(message)\n".utf8))
}

/// Input sources registered under this bundle ID (the bundle and its input modes).
func inputSources(bundleID: String) -> [TISInputSource] {
    let filter = [kTISPropertyBundleID as String: bundleID] as CFDictionary
    return TISCreateInputSourceList(filter, true)?.takeRetainedValue() as? [TISInputSource] ?? []
}

func inputModeID(_ source: TISInputSource) -> String? {
    guard let p = TISGetInputSourceProperty(source, kTISPropertyInputModeID) else { return nil }
    return Unmanaged<CFString>.fromOpaque(p).takeUnretainedValue() as String
}

/// docs/contracts/s3b.md section 13.3. Register the bundle (always: a bundle ID TIS already knows
/// may still carry the old two-mode list), enable the input method and its one mode, check that
/// the system now lists the input method as enabled, then disable the two modes of earlier
/// versions if TIS still lists them. Run on the installed copy, by scripts/install-ime.sh or by
/// the user after `brew install`. Exit 3 means the mode is not listed yet or the input method is
/// not accepted yet (log out and log in, then run it again); 1 is any other failure.
private func isEnabled(_ source: TISInputSource) -> Bool {
    guard let p = TISGetInputSourceProperty(source, kTISPropertyInputSourceIsEnabled) else { return false }
    return CFBooleanGetValue(Unmanaged<CFBoolean>.fromOpaque(p).takeUnretainedValue())
}

func install() -> Int32 {
    guard let bundleID = Bundle.main.bundleIdentifier else {
        say("install: no bundle identifier")
        return 1
    }
    // A failure here is not fatal by itself (an already registered bundle may report an error;
    // not measured), but it decides the exit code below: exit 3 means "registered, the list is
    // not refreshed yet", so a failed registration with no mode listed is an ordinary failure.
    let registered = TISRegisterInputSource(Bundle.main.bundleURL as CFURL) == noErr
    if !registered { say("install: warning: TISRegisterInputSource failed") }
    let sources = inputSources(bundleID: bundleID)
    guard let mode = sources.first(where: { inputModeID($0) == "\(bundleID).zhuyin" }) else {
        say(registered ? "install: the input mode is not listed yet" : "install: registration failed and the input mode is not listed")
        return registered ? 3 : 1
    }
    // The input method itself (the source whose ID is the bundle ID) must be enabled too, or the
    // mode is listed nowhere (measured 2026-10-04 with 0.1.0, which enabled only the mode).
    let parent = sources.first { inputModeID($0) == nil }
    guard parent.map({ TISEnableInputSource($0) == noErr }) ?? false,
          TISEnableInputSource(mode) == noErr else {
        say("install: TISEnableInputSource failed")
        return 1
    }
    // Measured 2026-10-04: before the first log out after registration, both calls return noErr
    // and this process's enabled list shows the mode, but not the input method itself, which
    // stays disabled. Only the input method appearing in the enabled list counts.
    let enabledNow = TISCreateInputSourceList(
        [kTISPropertyBundleID as String: bundleID] as CFDictionary, false)?
        .takeRetainedValue() as? [TISInputSource] ?? []
    guard enabledNow.contains(where: { inputModeID($0) == nil }) else {
        say("install: the system has not accepted the input method yet")
        return 3
    }
    // Carry over an ETen-only setup from the two-mode versions, unless a layout was chosen already.
    let enabled = { (id: String) in sources.contains { inputModeID($0) == id && isEnabled($0) } }
    if UserDefaults.standard.string(forKey: "layout") == nil,
       enabled("\(bundleID).eten"), !enabled("\(bundleID).standard") {
        UserDefaults.standard.set("eten", forKey: "layout")
    }
    let legacy: Set<String> = ["\(bundleID).standard", "\(bundleID).eten"]
    for source in sources where inputModeID(source).map(legacy.contains) ?? false {
        if TISDisableInputSource(source) != noErr {
            say("install: warning: TISDisableInputSource failed for an input mode of an earlier version")
        }
    }
    say("install: registered and enabled")
    return 0
}

/// The chosen keyboard layout, in the app's own UserDefaults domain (its bundle ID), key `layout`
/// (section 13.2). Only the running input method creates this; tests use MemoryLayoutStore.
@MainActor
final class DefaultsLayoutStore: LayoutStore {
    var layout: String? {
        get { UserDefaults.standard.string(forKey: "layout") }
        set { UserDefaults.standard.set(newValue, forKey: "layout") }
    }
}

@MainActor
func runServer() -> Never {
    guard let bundleID = Bundle.main.bundleIdentifier,
          let connection = Bundle.main.infoDictionary?["InputMethodConnectionName"] as? String,
          let resources = Bundle.main.resourceURL
    else {
        say("shanjie: not running from the app bundle")
        exit(1)
    }
    let app = NSApplication.shared
    guard let server = IMKServer(name: connection, bundleIdentifier: bundleID) else {
        say("shanjie: cannot create the input method server")
        exit(1)
    }
    // Section 5: the engine is built after the IMK server exists.
    App.shell = Shell(
        resources: resources.absoluteURL, panel: CandidatePanelAdapter(server: server),
        isSecureInput: { IsSecureEventInputEnabled() }, layoutStore: DefaultsLayoutStore())
    withExtendedLifetime(server) { app.run() }
    exit(0)
}

let arguments = CommandLine.arguments
if arguments.count == 1 {
    MainActor.assumeIsolated { runServer() }
} else if arguments.count == 2 && arguments[1] == "install" {
    exit(install())
} else if arguments.count == 2 && arguments[1] == "--selftest" {
    guard let resources = Bundle.main.resourceURL else {
        say("selftest: no Resources directory")
        exit(1)
    }
    exit(MainActor.assumeIsolated { Selftest.run(resources: resources.absoluteURL) })
} else {
    say("usage: shanjie [install | --selftest]")
    exit(64)
}
