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

/// McBopomofo's install(): register the bundle if TIS does not know it yet, then enable both
/// input modes. Run only by scripts/install-ime.sh, on the installed copy.
func install() -> Int32 {
    guard let bundleID = Bundle.main.bundleIdentifier else {
        say("install: no bundle identifier")
        return 1
    }
    if inputSources(bundleID: bundleID).isEmpty {
        guard TISRegisterInputSource(Bundle.main.bundleURL as CFURL) == noErr else {
            say("install: TISRegisterInputSource failed")
            return 1
        }
    }
    let modes = Set(InputMode.allCases.map { "\(bundleID).\($0.rawValue)" })
    var enabled = 0
    for source in inputSources(bundleID: bundleID) {
        guard let mode = inputModeID(source), modes.contains(mode) else { continue }
        guard TISEnableInputSource(source) == noErr else {
            say("install: TISEnableInputSource failed")
            return 1
        }
        enabled += 1
    }
    guard enabled == modes.count else {
        say("install: input modes not found after registration")
        return 1
    }
    say("install: registered and enabled")
    return 0
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
        isSecureInput: { IsSecureEventInputEnabled() })
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
