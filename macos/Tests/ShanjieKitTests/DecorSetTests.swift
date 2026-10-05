import AppKit
import XCTest
@testable import ShanjieKit

@MainActor
final class DecorSetTests: XCTestCase {
    private func thumb() -> NSView { FilledView(frame: NSRect(x: 0, y: 0, width: 2, height: 9), color: .tertiaryLabelColor) }

    /// A show adds the thumb straight into the row, then a widen animation (or a settle) ends with
    /// nothing held back: the thumb must stay tracked, or the next show leaves a second one behind.
    func testViewsAddedByShowSurviveFlushAndAreRemovedOnce() {
        let row = NSView(frame: NSRect(x: 0, y: 0, width: 100, height: 100))
        let decor = DecorSet()
        decor.replace(with: [thumb()], deferred: false, in: row)
        decor.flush(into: row)
        let next = thumb()
        decor.replace(with: [next], deferred: false, in: row)
        XCTAssertEqual(row.subviews, [next])
        decor.clear()
        XCTAssertTrue(row.subviews.isEmpty)
    }

    func testDeferredViewsArriveAtFlushAndAreTrackedThere() {
        let row = NSView(frame: NSRect(x: 0, y: 0, width: 100, height: 100))
        let decor = DecorSet()
        let a = thumb()
        decor.replace(with: [a], deferred: true, in: row)
        XCTAssertTrue(row.subviews.isEmpty)
        decor.flush(into: row)
        XCTAssertEqual(row.subviews, [a])
        decor.replace(with: [], deferred: false, in: row)
        XCTAssertTrue(row.subviews.isEmpty)
    }
}
