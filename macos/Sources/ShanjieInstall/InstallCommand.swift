import Foundation

/// docs/contracts/installer-v2.md section 9.2 / s3b.md section 13.3: what `shanjie install` prints
/// and exits with. Pure, so it is unit tested; `Shanjie/main.swift` only runs it and prints.
public enum InstallCommand {
    public struct Report: Equatable, Sendable {
        public var exitCode: Int32
        public var messages: [String]
        public init(exitCode: Int32, messages: [String]) {
            self.exitCode = exitCode
            self.messages = messages
        }
    }

    static let settingsSteps = "install: open System Settings > Keyboard > Input Sources, press +, choose Shanjie under Chinese (Traditional) and press Add"

    /// `acceptedAfterWait` is the result of `Registration.waitUntilAccepted` (only read for a
    /// skipped `notAccepted`).
    public static func report(_ result: Registration.Result, acceptedAfterWait: Bool) -> Report {
        var messages: [String] = []
        if result.registerFailed { messages.append("install: warning: TISRegisterInputSource failed") }
        switch result.outcome {
        case .modeNotListed:
            return Report(exitCode: 3, messages: messages + ["install: the input mode is not listed yet", settingsSteps])
        case .registrationFailed:
            return Report(exitCode: 1, messages: messages + ["install: registration failed and the input mode is not listed"])
        case .enableFailed:
            return Report(exitCode: 1, messages: messages + ["install: TISEnableInputSource failed"])
        case .notAccepted:
            if result.skipped, acceptedAfterWait {
                return Report(exitCode: 0, messages: messages + ["install: already enabled; registration skipped"])
            }
            return Report(exitCode: 3, messages: messages + ["install: the system has not accepted the input method yet", settingsSteps])
        case .done where result.skipped:
            return Report(exitCode: 0, messages: messages + ["install: already enabled; registration skipped"])
        case .done:
            if result.legacyDisableFailed {
                messages.append("install: warning: TISDisableInputSource failed for an input mode of an earlier version")
            }
            return Report(exitCode: 0, messages: messages + ["install: registered and enabled"])
        }
    }
}
