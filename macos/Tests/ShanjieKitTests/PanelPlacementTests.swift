import XCTest
@testable import ShanjieKit

final class PanelPlacementTests: XCTestCase {
    let screen = NSRect(x: 0, y: 0, width: 1000, height: 800)
    let size = NSSize(width: 200, height: 30)
    let line = NSRect(x: 300, y: 400, width: 8, height: 18)

    func place(_ line: NSRect?, last: NSPoint? = nil, screens: [NSRect]? = nil, rectScreen: Int? = 0, main: Int = 0) -> NSPoint {
        PanelPlacement.topLeft(lineRect: line, lastOrigin: last, size: size, alignOffset: 20,
                               screens: screens ?? [screen], rectScreen: rectScreen, main: main)
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
        let p = place(NSRect(x: 1990, y: 400, width: 8, height: 18), screens: [screen, right], rectScreen: 1)
        XCTAssertEqual(p.x, 1800)
        let q = place(NSRect(x: 1500, y: 400, width: 8, height: 18), screens: [screen, right], rectScreen: 1)
        XCTAssertEqual(q.x, 1480)
    }

    func testNoLineReusesTheLastOrigin() {
        XCTAssertEqual(place(nil, last: NSPoint(x: 11, y: 222)), NSPoint(x: 11, y: 222))
    }

    func testNoLineAndNoLastGoesBottomLeftOfMain() {
        let other = NSRect(x: 1000, y: 50, width: 1000, height: 700)
        XCTAssertEqual(place(nil, screens: [screen, other], rectScreen: nil, main: 1), NSPoint(x: 1000, y: 80))
    }
}
