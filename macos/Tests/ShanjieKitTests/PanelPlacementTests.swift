import XCTest
@testable import ShanjieKit

final class PanelPlacementTests: XCTestCase {
    let screen = NSRect(x: 0, y: 0, width: 1000, height: 800)
    let size = NSSize(width: 200, height: 30)
    let line = NSRect(x: 300, y: 400, width: 8, height: 18)

    /// `last` is the remembered line, the last one the client gave.
    func place(_ line: NSRect?, last: NSRect? = nil, screens: [NSRect]? = nil, main: Int = 0) -> NSPoint {
        PanelPlacement.topLeft(line: line, last: last, size: size, alignOffset: 20,
                               screens: (screens ?? [screen]).map { PanelPlacement.Screen(frame: $0, visibleFrame: $0) }, main: main).origin
    }

    func testBelowTheLineAlignedToTheText() {
        XCTAssertEqual(place(line), NSPoint(x: 280, y: 394))
    }

    func testNoRoomBelowGoesAbove() {
        let low = NSRect(x: 300, y: 10, width: 8, height: 18)
        XCTAssertEqual(place(low), NSPoint(x: 280, y: 10 + 18 + 6 + 30))
    }

    func testRightEdgeMovesLeft() {
        XCTAssertEqual(place(NSRect(x: 950, y: 400, width: 8, height: 18)).x, 800)
    }

    func testLeftEdgeSticks() {
        XCTAssertEqual(place(NSRect(x: 5, y: 400, width: 8, height: 18)).x, 0)
    }

    func testUsesTheScreenThatHoldsTheLine() {
        let right = NSRect(x: 1000, y: 0, width: 1000, height: 800)
        let p = place(NSRect(x: 1990, y: 400, width: 8, height: 18), screens: [screen, right])
        XCTAssertEqual(p.x, 1800)
        let q = place(NSRect(x: 1500, y: 400, width: 8, height: 18), screens: [screen, right])
        XCTAssertEqual(q.x, 1480)
    }

    /// No line: the last line the client gave is used and the origin is worked out for this size (not a remembered corner).
    /// The client's line wins over it.
    func testNoLineUsesTheLastLine() {
        XCTAssertEqual(place(nil, last: line), NSPoint(x: 280, y: 394))
        let other = NSRect(x: 600, y: 200, width: 8, height: 18)
        XCTAssertEqual(place(line, last: other), NSPoint(x: 280, y: 394))
        // On a second screen the remembered line keeps the panel on that screen.
        let right = NSRect(x: 1000, y: 0, width: 1000, height: 800)
        XCTAssertEqual(place(nil, last: NSRect(x: 1990, y: 400, width: 8, height: 18), screens: [screen, right]).x, 1800)
    }

    /// A remembered line no screen holds (its screen is gone) is dropped: the default corner, not a panel off the screen.
    func testLastLineOnNoScreenIsDropped() {
        XCTAssertEqual(place(nil, last: NSRect(x: 1500, y: -500, width: 8, height: 18)), NSPoint(x: 0, y: 30))
    }

    func testNoLineAndNoLastGoesBottomLeftOfMain() {
        let other = NSRect(x: 1000, y: 50, width: 1000, height: 700)
        XCTAssertEqual(place(nil, screens: [screen, other], main: 1), NSPoint(x: 1000, y: 80))
    }
}
