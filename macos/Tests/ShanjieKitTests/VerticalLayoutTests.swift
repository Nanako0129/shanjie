import AppKit
import XCTest
@testable import ShanjieKit

/// docs/contracts/candidate-vertical.md section 3.1: the vertical window's pure functions. The two measured values
/// (28 pt row pitch, 226 pt) are asserted as numbers on purpose: they come from the contract's section 1 (Apple Zhuyin,
/// 2026-10-10), so a change to either is a decision, not a refactor.
final class VerticalLayoutTests: XCTestCase {
    func testMeasuredValuesOfSectionOne() {
        XCTAssertEqual(VerticalLayout.rowPitch, 28)
        XCTAssertEqual(VerticalLayout.minWidth, 226)
        XCTAssertEqual(VerticalLayout.visibleRows, 9)
    }

    /// The selection box's lower edge in Apple's screenshots was 109, 137, 165, 193: 28 apart. Row n's y moves by the pitch.
    func testRowYStepsByThePitchFromTheTop() {
        let capsule: CGFloat = 24
        let y0 = VerticalLayout.rowY(0, capsuleHeight: capsule)
        XCTAssertEqual(y0, VerticalLayout.inset + (28 - capsule) / 2, "the capsule is centred in its pitch")
        for n in 0..<9 {
            XCTAssertEqual(VerticalLayout.rowY(n, capsuleHeight: capsule), y0 + CGFloat(n) * 28)
        }
        // The last of nine rows ends before the window's bottom inset.
        XCTAssertEqual(VerticalLayout.rowY(8, capsuleHeight: capsule) + capsule + (28 - capsule) / 2 + VerticalLayout.inset,
                       VerticalLayout.height(rows: 9))
    }

    func testHeightIsRowsTimesPitchPlusInsets() {
        XCTAssertEqual(VerticalLayout.height(rows: 9), 9 * 28 + 2 * VerticalLayout.inset)
        XCTAssertEqual(VerticalLayout.height(rows: 3), 3 * 28 + 2 * VerticalLayout.inset)
    }

    /// The numbers follow the visible rows: after the window scrolls by one, the first visible row is 1 again.
    @MainActor
    func testNumbersFollowTheVisibleRowsAfterScrolling() {
        let cells = CandidateCells()
        let list = (0..<12).map { "字\($0)" }
        let a = cells.update(candidates: Array(list[0..<9]), notes: Array(repeating: nil, count: 9), selected: 8, first: 0, columns: 0)
        XCTAssertEqual(a.cells.map(\.numberText), (1...9).map(String.init))
        let b = cells.update(candidates: Array(list[1..<10]), notes: Array(repeating: nil, count: 9), selected: 8, first: 1, columns: 0)
        XCTAssertEqual(b.cells.map(\.numberText), (1...9).map(String.init), "numbers restart at the first visible row")
        XCTAssertEqual(b.cells.map(\.text), Array(list[1..<10]))
        XCTAssertEqual(b.cells.map(\.position), Array(0..<9), "a click reports the row in the output")
        XCTAssertTrue(b.cells.allSatisfy { $0.numberText != "" && $0.showsNumber }, "every row shows its number")
    }

    // MARK: width: at least 226, widen-only

    func testWidthIsAtLeastTheMeasuredMinimum() {
        XCTAssertEqual(VerticalLayout.width(contentWidths: [], current: 0, gutter: 0), 226)
        XCTAssertEqual(VerticalLayout.width(contentWidths: [40, 60], current: 0, gutter: 0), 226)
    }

    func testWidthWidensForWiderContentAndNeverShrinks() {
        let side = VerticalLayout.sideInset * 2
        // A row wider than the minimum widens the window, by its width plus the side insets and the gutter.
        let wide = VerticalLayout.width(contentWidths: [100, 300], current: 226, gutter: 9)
        XCTAssertEqual(wide, 300 + side + 9)
        // Scrolling brings rows that are not wider: the width stays.
        XCTAssertEqual(VerticalLayout.width(contentWidths: [100, 120], current: wide, gutter: 9), wide)
        // Rows that are a little wider than before but still within the width so far: still the same.
        XCTAssertEqual(VerticalLayout.width(contentWidths: [290], current: wide, gutter: 9), wide)
        // A wider one widens it again, and the next narrow rows keep that.
        let wider = VerticalLayout.width(contentWidths: [320], current: wide, gutter: 9)
        XCTAssertEqual(wider, 320 + side + 9)
        XCTAssertEqual(VerticalLayout.width(contentWidths: [10], current: wider, gutter: 9), wider)
    }

    func testWidthIsAWholeNumberOfPoints() {
        XCTAssertEqual(VerticalLayout.width(contentWidths: [300.2], current: 0, gutter: 0), (300.2 + VerticalLayout.sideInset * 2).rounded(.up))
    }

    // MARK: scroll indicator

    func testThumbIsAbsentWhileEverythingFits() {
        XCTAssertNil(VerticalLayout.thumb(first: 0, total: 9, visible: 9, height: 262))
        XCTAssertNil(VerticalLayout.thumb(first: 0, total: 5, visible: 5, height: 152))
    }

    func testThumbMovesFromTheTopToTheBottomOfItsTrack() throws {
        let height = VerticalLayout.height(rows: 9)
        let top = try XCTUnwrap(VerticalLayout.thumb(first: 0, total: 90, visible: 9, height: height))
        let bottom = try XCTUnwrap(VerticalLayout.thumb(first: 81, total: 90, visible: 9, height: height))
        let mid = try XCTUnwrap(VerticalLayout.thumb(first: 40, total: 90, visible: 9, height: height))
        XCTAssertEqual(top.y, VerticalLayout.inset)
        XCTAssertEqual(bottom.y + bottom.height, height - VerticalLayout.inset)
        XCTAssertEqual(top.height, bottom.height)
        XCTAssertTrue(top.y < mid.y && mid.y < bottom.y)
        XCTAssertGreaterThanOrEqual(top.height, VerticalLayout.thumbMinHeight)
    }

    // MARK: position: s3b2 section 2.3 with the vertical panel size

    func testPlacementUsesTheVerticalSizeBelowTheLineAndAboveWhenThereIsNoRoom() {
        let size = NSSize(width: 226, height: VerticalLayout.height(rows: 9))
        let screen = NSRect(x: 0, y: 0, width: 1440, height: 900)
        // Room below: the top edge is the gap under the line.
        let line = NSRect(x: 300, y: 600, width: 8, height: 18)
        let below = PanelPlacement.topLeft(lineRect: line, lastOrigin: nil, size: size, alignOffset: 10, screens: [screen], rectScreen: 0, main: 0)
        XCTAssertEqual(below, NSPoint(x: 290, y: 600 - PanelPlacement.gap))
        // A line so low that the 262 pt panel does not fit below: it goes above the line.
        let low = NSRect(x: 300, y: 100, width: 8, height: 18)
        let above = PanelPlacement.topLeft(lineRect: low, lastOrigin: nil, size: size, alignOffset: 10, screens: [screen], rectScreen: 0, main: 0)
        XCTAssertEqual(above.y, low.maxY + PanelPlacement.gap + size.height)
        // The horizontal bar (28 pt) still fits below the same line: the vertical size is what moved it.
        let bar = PanelPlacement.topLeft(lineRect: low, lastOrigin: nil, size: NSSize(width: 100, height: 28), alignOffset: 10, screens: [screen], rectScreen: 0, main: 0)
        XCTAssertEqual(bar.y, low.minY - PanelPlacement.gap)
        // Clamped to the right edge by the panel's own width.
        let right = NSRect(x: 1400, y: 600, width: 8, height: 18)
        let clamped = PanelPlacement.topLeft(lineRect: right, lastOrigin: nil, size: size, alignOffset: 10, screens: [screen], rectScreen: 0, main: 0)
        XCTAssertEqual(clamped.x, 1440 - 226)
    }
}
