import Carbon
import Foundation

// docs/contracts/s3b.md section 13.3 and s3c-installer.md section 2.3: register the input method
// bundle, enable the input method and its one mode, check that the system took it, then disable
// the two modes of earlier versions. Shared by `shanjie install` (its own bundle) and the
// installer (the installed copy, never a path inside the installer: under App Translocation that
// is a random read-only path).

public enum Registration {
    public enum Outcome: Equatable, Sendable {
        /// Registered, enabled and listed as enabled.
        case done
        /// The mode is not listed after registering (the system has not loaded the bundle yet).
        case modeNotListed
        /// Enabled without an error, but the input method is not in the enabled list yet. Measured
        /// 2026-10-04: usual right after the first registration, until a log out and log in.
        case notAccepted
        /// Registration failed and the mode is not listed.
        case registrationFailed
        /// An enable call failed, or the input method source itself is missing.
        case enableFailed
    }

    public struct Result: Equatable, Sendable {
        public var outcome: Outcome
        /// TISRegisterInputSource returned an error (not fatal by itself).
        public var registerFailed = false
        /// Disabling a mode of an earlier version failed (does not change the outcome).
        public var legacyDisableFailed = false
    }

    /// Runs the whole sequence. `defaults` is the input method's own domain: `shanjie install`
    /// passes `UserDefaults.standard` (it runs as the input method), the installer passes
    /// `UserDefaults(suiteName: <bundle ID>)`.
    public static func run(bundleURL: URL, bundleID: String, defaults: UserDefaults) -> Result {
        // Always registered: a bundle ID TIS already knows may still carry the old two-mode list.
        // Whether an already registered bundle reports an error here is not measured; it only
        // decides between registrationFailed and modeNotListed below.
        let registered = TISRegisterInputSource(bundleURL as CFURL) == noErr
        var result = Result(outcome: .done, registerFailed: !registered)
        let sources = inputSources(bundleID: bundleID, includeAllInstalled: true)
        guard let mode = sources.first(where: { inputModeID($0) == "\(bundleID).zhuyin" }) else {
            result.outcome = registered ? .modeNotListed : .registrationFailed
            return result
        }
        // The input method itself must be enabled too, or the mode is listed nowhere (measured
        // 2026-10-04 with 0.1.0, which enabled only the mode).
        let parent = sources.first { inputModeID($0) == nil }
        guard parent.map({ TISEnableInputSource($0) == noErr }) ?? false,
              TISEnableInputSource(mode) == noErr else {
            result.outcome = .enableFailed
            return result
        }
        guard isAccepted(bundleID: bundleID) else {
            result.outcome = .notAccepted
            return result
        }

        // Carry over an ETen-only setup from the two-mode versions, unless a layout was chosen.
        let enabled = { (id: String) in sources.contains { inputModeID($0) == id && isEnabled($0) } }
        if defaults.string(forKey: "layout") == nil,
           enabled("\(bundleID).eten"), !enabled("\(bundleID).standard") {
            defaults.set("eten", forKey: "layout")
        }
        let legacy: Set<String> = ["\(bundleID).standard", "\(bundleID).eten"]
        for source in sources where inputModeID(source).map(legacy.contains) ?? false {
            if TISDisableInputSource(source) != noErr { result.legacyDisableFailed = true }
        }
        return result
    }

    /// Measured 2026-10-04: before the first log out after registration, both enable calls return
    /// noErr and this process's enabled list shows the mode, but not the input method itself,
    /// which stays disabled. Only the input method appearing in the enabled list counts.
    public static func isAccepted(bundleID: String) -> Bool {
        inputSources(bundleID: bundleID, includeAllInstalled: false).contains { inputModeID($0) == nil }
    }

    /// docs/contracts/installer-v2.md section 1.2: selects the input method's one mode among the
    /// enabled sources. False when it is not listed, not selectable or the call fails; whether the
    /// switch took is checked separately with `isCurrentMode` (it can return noErr and not switch).
    public static func selectMode(bundleID: String) -> Bool {
        let sources = inputSources(bundleID: bundleID, includeAllInstalled: false)
        let listed = sources.map { (modeID: inputModeID($0), selectable: isSelectCapable($0)) }
        guard let index = InstallerFlow.modeIndex(listed, bundleID: bundleID) else { return false }
        return TISSelectInputSource(sources[index]) == noErr
    }

    public static func isCurrentMode(bundleID: String) -> Bool {
        inputModeID(TISCopyCurrentKeyboardInputSource().takeRetainedValue()) == "\(bundleID).zhuyin"
    }

    static func inputSources(bundleID: String, includeAllInstalled: Bool) -> [TISInputSource] {
        let filter = [kTISPropertyBundleID as String: bundleID] as CFDictionary
        return TISCreateInputSourceList(filter, includeAllInstalled)?.takeRetainedValue() as? [TISInputSource] ?? []
    }

    static func inputModeID(_ source: TISInputSource) -> String? {
        guard let p = TISGetInputSourceProperty(source, kTISPropertyInputModeID) else { return nil }
        return Unmanaged<CFString>.fromOpaque(p).takeUnretainedValue() as String
    }

    static func isEnabled(_ source: TISInputSource) -> Bool {
        boolProperty(source, kTISPropertyInputSourceIsEnabled)
    }

    static func isSelectCapable(_ source: TISInputSource) -> Bool {
        boolProperty(source, kTISPropertyInputSourceIsSelectCapable)
    }

    private static func boolProperty(_ source: TISInputSource, _ key: CFString) -> Bool {
        guard let p = TISGetInputSourceProperty(source, key) else { return false }
        return CFBooleanGetValue(Unmanaged<CFBoolean>.fromOpaque(p).takeUnretainedValue())
    }
}
