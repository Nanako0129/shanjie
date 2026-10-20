import ShanjieInstall
import XCTest

// docs/contracts/installer-v2.md section 3, acceptance 1.
final class InstallerFlowTests: XCTestCase {
    func testEveryOutcomeHasItsScreen() {
        XCTAssertEqual(InstallerFlow.next(after: .done), .tryIt)
        XCTAssertEqual(InstallerFlow.next(after: .modeNotListed), .activate)
        XCTAssertEqual(InstallerFlow.next(after: .notAccepted), .waiting)
        XCTAssertEqual(InstallerFlow.next(after: .registrationFailed), .failed)
        XCTAssertEqual(InstallerFlow.next(after: .enableFailed), .failed)
    }

    /// system-install.md section 2.4, the table of screens after a location change, every cell.
    func testMovedScreens() {
        let all: [Registration.Outcome] = [.done, .modeNotListed, .notAccepted, .registrationFailed, .enableFailed]
        let moved: [Registration.Outcome: InstallerScreen] = [
            .done: .logOut, .notAccepted: .logOutThenAdd, .modeNotListed: .logOutThenAdd,
            .registrationFailed: .failedLogOut, .enableFailed: .failedLogOut,
        ]
        for outcome in all {
            XCTAssertEqual(InstallerFlow.next(after: outcome, moved: true), moved[outcome], "moved, \(outcome)")
            XCTAssertEqual(InstallerFlow.next(after: outcome, moved: false), InstallerFlow.next(after: outcome), "not moved, \(outcome)")
        }
        // Never a poll or a trip to System Settings before the log out.
        for outcome in all {
            XCTAssertNotEqual(InstallerFlow.next(after: outcome, moved: true), .waiting)
            XCTAssertNotEqual(InstallerFlow.next(after: outcome, moved: true), .activate)
        }
    }

    func testPicksTheSelectableZhuyinMode() {
        let id = "com.nyanako.inputmethod.shanjie"
        // The input method itself, a leftover mode of an earlier version, then the one mode.
        let listed: [(modeID: String?, selectable: Bool)] = [(nil, false), ("\(id).standard", true), ("\(id).zhuyin", true)]
        XCTAssertEqual(InstallerFlow.modeIndex(listed, bundleID: id), 2)
    }

    /// In-memory preferences: nothing reaches cfprefsd or ~/Library/Preferences.
    private final class MemoryDefaults: UserDefaults {
        var store: [String: Any] = [:]
        override func object(forKey key: String) -> Any? { store[key] }
        override func set(_ value: Any?, forKey key: String) { store[key] = value }
        override func synchronize() -> Bool { true }
    }
    private let id = "com.nyanako.inputmethod.shanjie"
    private func memory() -> MemoryDefaults { MemoryDefaults(suiteName: "InstallerFlowTests.\(UUID().uuidString)")! }

    /// app-sandbox.md section 2.7, the installer's copy path (main.swift calls exactly this): with an
    /// ETen-only setup, `layout=eten` is written and on disk before install-ime.sh swaps the files,
    /// and the swap's result is what the copy step returns.
    func testCopyCarriesOverEtenAndWaitsForTheFileBeforeTheSwap() {
        let defaults = memory()
        var checks = 0, sleeps: [TimeInterval] = [], swapped = false
        let result = InstallerFlow.copy(
            bundleID: id, modes: ["\(id).eten"], defaults: { $0 == self.id ? defaults : nil },
            layoutOnDisk: { checks += 1; return $0 == self.id && checks >= 3 },
            sleep: { sleeps.append($0) },
            swap: { () -> Int in
                XCTAssertEqual(defaults.string(forKey: "layout"), "eten", "carried over before the swap")
                XCTAssertEqual(checks, 3, "the swap waited until the file held layout")
                swapped = true
                return 7
            })
        XCTAssertTrue(swapped)
        XCTAssertEqual(result, 7)
        XCTAssertEqual(sleeps, [0.25, 0.25])
    }

    /// The wait is bounded: a file that never holds layout still lets the install go on.
    func testCopyWaitIsBounded() {
        var checks = 0, slept: TimeInterval = 0, swapped = false
        InstallerFlow.copy(bundleID: id, modes: ["\(id).eten"], defaults: { _ in self.memory() },
                           layoutOnDisk: { _ in checks += 1; return false }, timeout: 1, interval: 0.25,
                           sleep: { slept += $0 }, swap: { swapped = true })
        XCTAssertTrue(swapped)
        XCTAssertEqual(slept, 1)
        XCTAssertEqual(checks, 5)
    }

    /// Nothing carried over (a chosen layout, no ETen-only setup, or no installed copy): no write and
    /// no wait, only the swap.
    func testCopyWithoutCarryOverOnlySwaps() {
        for (layout, modes) in [("standard", ["\(id).eten"]), (nil, ["\(id).eten", "\(id).standard"]), (nil, ["\(id).zhuyin"])] as [(String?, Set<String>)] {
            let defaults = memory()
            if let layout { defaults.set(layout, forKey: "layout") }
            var swapped = false
            InstallerFlow.copy(bundleID: id, modes: modes, defaults: { _ in defaults },
                               layoutOnDisk: { _ in XCTFail("waited without a carry-over"); return true },
                               sleep: { _ in XCTFail("slept") }, swap: { swapped = true })
            XCTAssertTrue(swapped)
            XCTAssertEqual(defaults.string(forKey: "layout"), layout)
        }
        var swapped = false
        InstallerFlow.copy(bundleID: nil, modes: ["\(id).eten"], defaults: { _ in XCTFail("no bundle ID, no defaults"); return nil },
                           layoutOnDisk: { _ in true }, swap: { swapped = true })
        XCTAssertTrue(swapped)
    }

    /// The disk check reads the preferences file itself.
    func testLayoutOnDiskReadsTheFile() throws {
        let home = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: home) }
        let prefs = home.appendingPathComponent("Library/Preferences")
        try FileManager.default.createDirectory(at: prefs, withIntermediateDirectories: true)
        XCTAssertFalse(InstallerFlow.layoutOnDisk(bundleID: id, home: home.path), "no file")
        let file = prefs.appendingPathComponent("\(id).plist")
        try PropertyListSerialization.data(fromPropertyList: ["other": 1], format: .binary, options: 0).write(to: file)
        XCTAssertFalse(InstallerFlow.layoutOnDisk(bundleID: id, home: home.path), "no layout key")
        try PropertyListSerialization.data(fromPropertyList: ["layout": "eten"], format: .binary, options: 0).write(to: file)
        XCTAssertTrue(InstallerFlow.layoutOnDisk(bundleID: id, home: home.path))
    }

    /// Only an ETen-only setup with no chosen layout carries over; a chosen layout is never rewritten.
    func testEtenCarryOverDecision() {
        let id = "com.nyanako.inputmethod.shanjie"
        let eten: Set<String> = ["\(id).eten"]
        XCTAssertTrue(Registration.carriesOverEten(layout: nil, enabledModeIDs: eten, bundleID: id))
        XCTAssertFalse(Registration.carriesOverEten(layout: "standard", enabledModeIDs: eten, bundleID: id))
        XCTAssertFalse(Registration.carriesOverEten(layout: nil, enabledModeIDs: eten.union(["\(id).standard"]), bundleID: id))
        XCTAssertFalse(Registration.carriesOverEten(layout: nil, enabledModeIDs: ["\(id).standard"], bundleID: id))
        XCTAssertFalse(Registration.carriesOverEten(layout: nil, enabledModeIDs: ["\(id).zhuyin"], bundleID: id))
        XCTAssertFalse(Registration.carriesOverEten(layout: nil, enabledModeIDs: ["other.eten"], bundleID: id))
    }

    func testNoModeWhenMissingOrNotSelectable() {
        let id = "com.nyanako.inputmethod.shanjie"
        XCTAssertNil(InstallerFlow.modeIndex([(nil, false), ("\(id).eten", true)], bundleID: id))
        XCTAssertNil(InstallerFlow.modeIndex([("\(id).zhuyin", false)], bundleID: id))
        XCTAssertNil(InstallerFlow.modeIndex([("other.zhuyin", true)], bundleID: id))
    }
}
