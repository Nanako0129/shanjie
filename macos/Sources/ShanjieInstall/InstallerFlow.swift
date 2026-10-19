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
