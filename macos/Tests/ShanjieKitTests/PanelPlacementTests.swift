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

    /// A remembered line no screen holds (its screen is gone) is not used: the default corner, not a panel off the screen.
    /// The client's own line off every screen is still used, as on main.
    func testLastLineOnNoScreenIsDropped() {
        let off = NSRect(x: 300, y: -500, width: 8, height: 18)
        XCTAssertEqual(place(nil, last: off), NSPoint(x: 0, y: 30))
        XCTAssertEqual(place(off), NSPoint(x: 280, y: -500 + 18 + 6 + 30), "the client's line: flipped above it, as on main")
        // The test is the screen's full frame, not its visible frame: a remembered line behind the Dock is still used.
        let docked = PanelPlacement.Screen(frame: screen, visibleFrame: NSRect(x: 0, y: 80, width: 1000, height: 695))
        let behindDock = NSRect(x: 300, y: 20, width: 8, height: 18)
        let p = PanelPlacement.topLeft(line: nil, last: behindDock, size: size, alignOffset: 20, screens: [docked], main: 0).origin
        XCTAssertEqual(p, NSPoint(x: 280, y: 20 + 18 + 6 + 30))
    }

    func testNoLineAndNoLastGoesBottomLeftOfMain() {
        let other = NSRect(x: 1000, y: 50, width: 1000, height: 700)
        XCTAssertEqual(place(nil, screens: [screen, other], main: 1), NSPoint(x: 1000, y: 80))
    }

    // docs/contracts/line-jump-guard.md section 3, items 1-3. Rectangles are the diagnostic trace's (x, y, width x height).
    func r(_ x: CGFloat, _ y: CGFloat, _ h: CGFloat = 22) -> NSRect { NSRect(x: x, y: y, width: 1, height: h) }

    /// Item 1: the three jumps Orca reported mid-composition (2026-10-13) are not used; its next line on the input row is.
    func testOrcaJumpsAreNotUsed() {
        XCTAssertEqual(PanelPlacement.steadyLine(r(472, 319), anchor: r(434, 88)), r(434, 88))
        var anchor = r(572, 88)
        for jump in [r(1002, 928), r(1018, 928), r(2504, 739)] {
            anchor = PanelPlacement.steadyLine(jump, anchor: anchor)!
            XCTAssertEqual(anchor, r(572, 88), "\(jump)")
        }
        XCTAssertEqual(PanelPlacement.steadyLine(r(622, 88), anchor: anchor), r(622, 88))
    }

    /// Item 2: moves along the line and to the next line (one line height down, y up in AppKit) are used, twice in a row too.
    func testNormalMovesAreUsed() {
        XCTAssertEqual(PanelPlacement.steadyLine(r(450, 88), anchor: r(434, 88)), r(450, 88))
        XCTAssertEqual(PanelPlacement.steadyLine(r(418, 87, 23), anchor: r(402, 88)), r(418, 87, 23))
        let second = PanelPlacement.steadyLine(r(10, 66), anchor: r(900, 88))
        XCTAssertEqual(second, r(10, 66))
        XCTAssertEqual(PanelPlacement.steadyLine(r(10, 44), anchor: second), r(10, 44))
    }

    /// Item 3: the first line of a composition is used as given, no line stays none, and a zero-height anchor counts 16 pt.
    func testFirstLineNoLineAndZeroHeight() {
        XCTAssertEqual(PanelPlacement.steadyLine(r(472, 319), anchor: nil), r(472, 319))
        XCTAssertNil(PanelPlacement.steadyLine(nil, anchor: r(434, 88)))
        let flat = r(100, 100, 0)
        XCTAssertEqual(PanelPlacement.steadyLine(r(100, 132), anchor: flat), r(100, 132))
        XCTAssertEqual(PanelPlacement.steadyLine(r(100, 133), anchor: flat), flat)
    }
}
