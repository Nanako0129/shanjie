import Carbon
import Foundation

// docs/contracts/s3b.md section 13.3 and s3c-installer.md section 2.3: register the input method
// bundle, enable the input method and its one mode, check that the system took it, then disable
// the two modes of earlier versions. Amended by installer-v2.md section 9: that sequence runs only
// when the system does not know the mode yet or an earlier version's mode is still enabled; with
// the input method already accepted, or known but not accepted, nothing is changed (a register or
// enable call then has no effect, measured, and breaks the Caps Lock switch). Shared by `shanjie install` (its own bundle) and the
// installer (the installed copy, never a path inside the installer: under App Translocation that
// is a random read-only path).

public enum Registration {
    public enum Outcome: Equatable, Sendable {
        /// Registered, enabled and listed as enabled.
        case done
        /// The mode is not listed after registering (the system has not loaded the bundle yet).
        case modeNotListed
        /// The input method is not in the enabled list. Either the enable calls ran and the system
        /// has not taken it (measured: until the user adds it in System Settings > Keyboard >
        /// Input Sources), or, with `skipped`, nothing was called and the caller may wait.
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
        /// installer-v2.md section 9.2: only the state was read, no TIS call changed anything.
        public var skipped = false

        public init(outcome: Outcome, registerFailed: Bool = false, legacyDisableFailed: Bool = false, skipped: Bool = false) {
            self.outcome = outcome
            self.registerFailed = registerFailed
            self.legacyDisableFailed = legacyDisableFailed
            self.skipped = skipped
        }
    }

    public enum Decision: Equatable, Sendable {
        case skipDone, skipNotAccepted, fullFlow
    }

    /// What `run` reads before it changes anything.
    public struct State: Equatable, Sendable {
        public var modeListed: Bool
        public var accepted: Bool
        public var legacyEnabled: Bool
        public init(modeListed: Bool, accepted: Bool, legacyEnabled: Bool) {
            self.modeListed = modeListed
            self.accepted = accepted
            self.legacyEnabled = legacyEnabled
        }
    }

    /// installer-v2.md section 9.2 item 1. An unknown mode (first install) or an enabled mode of an
    /// earlier version needs the whole sequence; otherwise nothing is changed.
    public static func decide(modeListed: Bool, accepted: Bool, legacyEnabled: Bool) -> Decision {
        if !modeListed || legacyEnabled { return .fullFlow }
        return accepted ? .skipDone : .skipNotAccepted
    }

    static func readState(bundleID: String) -> State {
        let all = inputSources(bundleID: bundleID, includeAllInstalled: true)
        let legacy: Set<String> = ["\(bundleID).standard", "\(bundleID).eten"]
        return State(
            modeListed: all.contains { inputModeID($0) == "\(bundleID).zhuyin" },
            accepted: isAccepted(bundleID: bundleID),
            legacyEnabled: all.contains { inputModeID($0).map(legacy.contains) ?? false && isEnabled($0) })
    }

    /// Runs the whole sequence. `defaults` is the input method's own domain: `shanjie install`
    /// passes `UserDefaults.standard` (it runs as the input method), the installer passes
    /// `UserDefaults(suiteName: <bundle ID>)`.
    /// Reads the state first and changes nothing unless `decide` says `fullFlow`. `readState` and
    /// `fullFlow` are injected only by tests, to count the calls that would change something.
    public static func run(
        bundleURL: URL, bundleID: String, defaults: UserDefaults,
        readState: ((String) -> State)? = nil,
        fullFlow: ((URL, String, UserDefaults) -> Result)? = nil
    ) -> Result {
        let state = (readState ?? Self.readState(bundleID:))(bundleID)
        switch decide(modeListed: state.modeListed, accepted: state.accepted, legacyEnabled: state.legacyEnabled) {
        case .skipDone: return Result(outcome: .done, skipped: true)
        case .skipNotAccepted: return Result(outcome: .notAccepted, skipped: true)
        case .fullFlow: return (fullFlow ?? Self.fullFlow(bundleURL:bundleID:defaults:))(bundleURL, bundleID, defaults)
        }
    }

    /// Waits, without calling anything that changes TIS state, for the system to take a skipped
    /// `notAccepted` (right after the files are swapped it may list the input method as disabled
    /// for a moment). True when the result is done or the input method became accepted in time.
    public static func waitUntilAccepted(
        _ result: Result, interval: TimeInterval = 0.5, limit: TimeInterval = 5,
        isAccepted: () -> Bool, sleep: (TimeInterval) -> Void = Thread.sleep(forTimeInterval:)
    ) -> Bool {
        if result.outcome == .done { return true }
        guard result.outcome == .notAccepted, result.skipped else { return false }
        var waited: TimeInterval = 0
        while waited < limit {
            sleep(interval)
            waited += interval
            if isAccepted() { return true }
        }
        return false
    }

    static func fullFlow(bundleURL: URL, bundleID: String, defaults: UserDefaults) -> Result {
        // Registered here: a bundle ID TIS already knows may still carry the old two-mode list.
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
