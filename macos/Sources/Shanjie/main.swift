import AppKit
import Carbon
import InputMethodKit
import ShanjieInstall
import ShanjieKit

// docs/contracts/s3b.md sections 4, 5 and 9. Arguments: none (run as the input method), exactly
// `install`, or exactly `--selftest`. Anything else exits non-zero before touching anything.
// Output is fixed text only (R2): no paths, bundle IDs or input.

func say(_ message: StaticString) {
    FileHandle.standardError.write(Data("\(message)\n".utf8))
}

/// docs/contracts/s3b.md section 13.3, through ShanjieInstall (shared with the installer). Run on
/// the installed copy, by scripts/install-ime.sh or by the user after `brew install`. Exit 3 means
/// the mode is not listed yet or the input method is not accepted yet (log out and log in, then
/// run it again); 1 is any other failure.
func install() -> Int32 {
    guard let bundleID = Bundle.main.bundleIdentifier else {
        say("install: no bundle identifier")
        return 1
    }
    let result = Registration.run(bundleURL: Bundle.main.bundleURL, bundleID: bundleID, defaults: .standard)
    if result.registerFailed { say("install: warning: TISRegisterInputSource failed") }
    switch result.outcome {
    case .modeNotListed:
        say("install: the input mode is not listed yet")
        return 3
    case .registrationFailed:
        say("install: registration failed and the input mode is not listed")
        return 1
    case .enableFailed:
        say("install: TISEnableInputSource failed")
        return 1
    case .notAccepted:
        say("install: the system has not accepted the input method yet")
        return 3
    case .done:
        if result.legacyDisableFailed {
            say("install: warning: TISDisableInputSource failed for an input mode of an earlier version")
        }
        say("install: registered and enabled")
        return 0
    }
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
