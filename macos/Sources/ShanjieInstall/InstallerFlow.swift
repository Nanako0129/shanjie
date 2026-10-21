import Foundation
import os

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
    /// system-install.md section 2.4: after a location change, "log out and back in" (one OK button).
    case logOut
    /// system-install.md section 2.4 (revision of 2026-10-21): after a first "This Mac" install that the system has not
    /// accepted or listed yet: log out and back in, then add Shanjie in System Settings (one OK button).
    case logOutThenAdd
    /// The failure screen with the same log-out sentence.
    case failedLogOut
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

    /// system-install.md section 2.4: after a location change the system shows the input method
    /// only after a log out (P1), so there is no polling and no trip to System Settings.
    /// `firstInstall` is `InstallDecision.firstInstall`, known before any TIS call.
    public static func next(after outcome: Registration.Outcome, moved: Bool, firstInstall: Bool) -> InstallerScreen {
        guard moved else { return next(after: outcome) }
        switch outcome {
        case .done: return .logOut
        // A first install the system has not accepted is not in the input menu after the log out either (seen on a VM,
        // 2026-10-21): the user adds it in System Settings once logged back in. A move is listed again after the log out
        // (P1), so it is never told to add it.
        case .notAccepted, .modeNotListed: return firstInstall ? .logOutThenAdd : .logOut
        case .registrationFailed, .enableFailed: return .failedLogOut
        }
    }

    /// system-install.md section 2.4 (second revision, 2026-10-21): whether the installer watches for System Settings coming to the
    /// front after this registration, to bring its last page back (the system opens System Settings at the enable call). Only for the
    /// two log-out pages, whose text says to answer an allow question first, and only when the enable call ran: a skipped
    /// registration, or one that stopped before enabling (modeNotListed, registrationFailed), never opens System Settings. Pages that
    /// send the user to System Settings (activate, failed) never call back; the poll's waiting screen calls back when it ends.
    public static func watchesSystemSettings(after result: Registration.Result, screen: InstallerScreen) -> Bool {
        let enableRan = !result.skipped && [.done, .notAccepted, .enableFailed].contains(result.outcome)
        return enableRan && (screen == .logOut || screen == .logOutThenAdd)
    }

    /// system-install.md section 2.4 item 4: one static line per installer registration, read with
    /// `log show --predicate 'subsystem == "com.nyanako.shanjie.installer"'` (section 4.3).
    public static func logRegistration(moved: Bool) {
        if moved { log.notice("registration: forced (location changed)") } else { log.notice("registration: as before") }
    }

    /// After a location change the installer waits for the system to accept the input method; one static line for each end of that
    /// wait, with whether the installer was in front (the call-back itself is `watchSystemSettings`, not this wait).
    public static func logReturn(accepted: Bool, wasActive: Bool) {
        switch (accepted, wasActive) {
        case (true, false): log.notice("return: accepted, not in front")
        case (true, true): log.notice("return: accepted, in front")
        case (false, _): log.notice("return: not accepted within the wait")
        }
    }

    /// The outcome of that registration, one static line, so a VM run shows which screen followed and why.
    public static func logOutcome(_ outcome: Registration.Outcome) {
        switch outcome {
        case .done: log.notice("registration outcome: done")
        case .modeNotListed: log.notice("registration outcome: modeNotListed")
        case .notAccepted: log.notice("registration outcome: notAccepted")
        case .registrationFailed: log.notice("registration outcome: registrationFailed")
        case .enableFailed: log.notice("registration outcome: enableFailed")
        }
    }

    /// app-sandbox.md section 2.7: the installer's copy step, as its copy path runs it, with the
    /// installed copy's bundle ID and enabled input modes read at the start. The ETen carry-over runs
    /// first, before `swap` (install-ime.sh) swaps the files: once the sandboxed version has started,
    /// the preferences live in its container and a value written outside it would be silently lost.
    /// When the carry-over wrote `layout`, the swap waits (up to `timeout`, polling `layoutOnDisk`)
    /// until the preferences file on disk holds it, since the first sandboxed launch moves the file,
    /// not cfprefsd's cache; if it never appears, a static line is logged and the install goes on.
    public static func copy<T>(
        bundleID: String?, modes: Set<String>,
        defaults: (String) -> UserDefaults? = { UserDefaults(suiteName: $0) },
        layoutOnDisk: (String) -> Bool,
        timeout: TimeInterval = 10, interval: TimeInterval = 0.25,
        sleep: (TimeInterval) -> Void = { Thread.sleep(forTimeInterval: $0) },
        swap: () -> T
    ) -> T {
        if let bundleID, let d = defaults(bundleID),
           Registration.carryOverEten(enabledModeIDs: modes, bundleID: bundleID, defaults: d),
           !poll(timeout: timeout, interval: interval, sleep: sleep, until: { layoutOnDisk(bundleID) }) {
            log.error("layout carry-over not on disk before the swap; continuing")
        }
        return swap()
    }

    /// True as soon as `until` holds, checked first at once and then after each `interval`; false
    /// once `timeout` has passed by the sum of the sleeps.
    static func poll(timeout: TimeInterval, interval: TimeInterval, sleep: (TimeInterval) -> Void, until: () -> Bool) -> Bool {
        var waited: TimeInterval = 0
        while !until() {
            if waited >= timeout { return false }
            sleep(interval)
            waited += interval
        }
        return true
    }

    /// Whether the preferences file of `bundleID` under `home` holds a `layout` key, read from disk.
    public static func layoutOnDisk(bundleID: String, home: String) -> Bool {
        let url = URL(fileURLWithPath: home + "/Library/Preferences/\(bundleID).plist")
        guard let data = try? Data(contentsOf: url),
              let plist = try? PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any] else { return false }
        return plist["layout"] != nil
    }

    private static let log = Logger(subsystem: "com.nyanako.shanjie.installer", category: "install")

    /// Section 1.2: the one source 「切換到善解」 selects, given each listed source's input mode ID
    /// and whether it is select-capable. The same bundle ID also lists the input method itself
    /// (no mode ID, not selectable) and, when disabling them failed, the modes of earlier
    /// versions; picking either would silently not switch (security review B-1).
    public static func modeIndex(_ sources: [(modeID: String?, selectable: Bool)], bundleID: String) -> Int? {
        sources.firstIndex { $0.modeID == "\(bundleID).zhuyin" && $0.selectable }
    }
}
