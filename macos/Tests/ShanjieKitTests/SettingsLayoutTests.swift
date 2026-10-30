import XCTest
@testable import ShanjieKit

/// docs/contracts/settings-fixed-size.md acceptance 1.
final class SettingsLayoutTests: XCTestCase {
    func testTallScreenGetsThePreferredHeight() {
        XCTAssertEqual(SettingsLayout.frameHeight(visibleHeight: 900), 620)
    }

    func testShortScreenShrinksTheWindowToTheVisibleHeight() {
        XCTAssertEqual(SettingsLayout.frameHeight(visibleHeight: 500), 500)
    }

    func testExactFit() {
        XCTAssertEqual(SettingsLayout.frameHeight(visibleHeight: 620), 620)
    }
}
