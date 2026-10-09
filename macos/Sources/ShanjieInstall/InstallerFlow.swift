import Foundation

/// docs/contracts/installer-v2.md section 1.5: which screen follows an enable attempt. Pure, so it
/// is unit tested; the 30 s wait for `.waiting` (s3c-installer.md section 2.4) stays in the
/// installer.
public enum InstallerScreen: Equatable, Sendable {
    case tryIt
    /// The system has not listed or accepted the input method: the user adds it in System Settings.
    case activate
    /// A status of the install screen while polling, not a screen of its own.
    case waiting
    case failed
}

public enum InstallerFlow {
    public static func next(after outcome: Registration.Outcome) -> InstallerScreen {
        switch outcome {
        case .done: .tryIt
        case .modeNotListed: .activate
        case .notAccepted: .waiting
        case .registrationFailed, .enableFailed: .failed
        }
    }

    /// app-sandbox.md section 2.7: the installer's copy step. The ETen carry-over runs first, before
    /// install-ime.sh swaps the files: once the sandboxed version has started, the preferences live
    /// in its container and a value written outside it would be silently lost.
    public static func copy<T>(carryOver: () -> Void, swap: () -> T) -> T {
        carryOver()
        return swap()
    }

    /// Section 1.2: the one source 「切換到善解」 selects, given each listed source's input mode ID
    /// and whether it is select-capable. The same bundle ID also lists the input method itself
    /// (no mode ID, not selectable) and, when disabling them failed, the modes of earlier
    /// versions; picking either would silently not switch (security review B-1).
    public static func modeIndex(_ sources: [(modeID: String?, selectable: Bool)], bundleID: String) -> Int? {
        sources.firstIndex { $0.modeID == "\(bundleID).zhuyin" && $0.selectable }
    }
}
