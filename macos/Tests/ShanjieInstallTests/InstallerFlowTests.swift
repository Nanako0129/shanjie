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

    func testNoModeWhenMissingOrNotSelectable() {
        let id = "com.nyanako.inputmethod.shanjie"
        XCTAssertNil(InstallerFlow.modeIndex([(nil, false), ("\(id).eten", true)], bundleID: id))
        XCTAssertNil(InstallerFlow.modeIndex([("\(id).zhuyin", false)], bundleID: id))
        XCTAssertNil(InstallerFlow.modeIndex([("other.zhuyin", true)], bundleID: id))
    }
}
