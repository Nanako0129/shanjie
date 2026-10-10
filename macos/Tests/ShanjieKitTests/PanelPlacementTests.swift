import XCTest
@testable import ShanjieKit

final class PanelPlacementTests: XCTestCase {
    let screen = NSRect(x: 0, y: 0, width: 1000, height: 800)
    let size = NSSize(width: 200, height: 30)
    let line = NSRect(x: 300, y: 400, width: 8, height: 18)

    /// `last` is the remembered line: the caller passes the client's line, else that one.
    func place(_ line: NSRect?, last: NSRect? = nil, screens: [NSRect]? = nil, main: Int = 0) -> NSPoint {
        PanelPlacement.topLeft(line: line ?? last, size: size, alignOffset: 20,
                               screens: (screens ?? [screen]).map { PanelPlacement.Screen(frame: $0, visibleFrame: $0) }, main: main)
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
    func testNoLineUsesTheLastLine() {
        XCTAssertEqual(place(nil, last: line), place(line))
        XCTAssertEqual(place(nil, last: line), NSPoint(x: 280, y: 394))
    }

    /// A stale line (an unplugged screen, a panel taller than the room) must not put the panel below the visible frame.
    func testBottomEdgeIsClamped() {
        // A line below the screen: it flips above, which is on screen.
        let stale = NSRect(x: 300, y: -500, width: 8, height: 18)
        let p = place(stale)
        XCTAssertGreaterThanOrEqual(p.y - size.height, screen.minY)
        // A line so high that "above" would leave the top, and a panel that fits neither way: the top stays inside.
        let tall = NSSize(width: 200, height: 900)
        let q = PanelPlacement.topLeft(line: NSRect(x: 300, y: 400, width: 8, height: 18), size: tall, alignOffset: 20,
                                       screens: [PanelPlacement.Screen(frame: screen, visibleFrame: screen)], main: 0)
        XCTAssertEqual(q.y, screen.minY + tall.height, "the bottom edge stays on the screen")
    }

    func testNoLineAndNoLastGoesBottomLeftOfMain() {
        let other = NSRect(x: 1000, y: 50, width: 1000, height: 700)
        XCTAssertEqual(place(nil, screens: [screen, other], main: 1), NSPoint(x: 1000, y: 80))
    }
}
