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

    func testPicksTheSelectableZhuyinMode() {
        let id = "com.nyanako.inputmethod.shanjie"
        // The input method itself, a leftover mode of an earlier version, then the one mode.
        let listed: [(modeID: String?, selectable: Bool)] = [(nil, false), ("\(id).standard", true), ("\(id).zhuyin", true)]
        XCTAssertEqual(InstallerFlow.modeIndex(listed, bundleID: id), 2)
    }

    /// app-sandbox.md section 2.7: the ETen carry-over is written before install-ime.sh swaps the
    /// files, and the swap's result is what the copy step returns.
    func testEtenCarryOverRunsBeforeTheSwap() {
        var events: [String] = []
        let result = InstallerFlow.copy(carryOver: { events.append("carryOver") }, swap: { () -> Int in
            events.append("swap")
            return 7
        })
        XCTAssertEqual(events, ["carryOver", "swap"])
        XCTAssertEqual(result, 7)
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
