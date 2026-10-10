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

    /// The numbers follow the visible rows: after the window scrolls by one, the first visible row is 1 again, and the
    /// cells that stay on screen are the same cells with a new number (a scroll makes one cell, not nine).
    @MainActor
    func testNumbersFollowTheVisibleRowsAndScrollingKeepsTheCells() {
        let cells = CandidateCells()
        let list = (0..<12).map { "字\($0)" }
        let none = [String?](repeating: nil, count: 9)
        let a = cells.update(candidates: Array(list[0..<9]), notes: none, selected: 8, first: 0, columns: 0, renumber: true)
        XCTAssertEqual(a.cells.map(\.numberText), (1...9).map(String.init))
        let b = cells.update(candidates: Array(list[1..<10]), notes: none, selected: 8, first: 1, columns: 0, renumber: true)
        XCTAssertEqual(b.cells.map(\.numberText), (1...9).map(String.init), "numbers restart at the first visible row")
        XCTAssertEqual(b.cells.map(\.text), Array(list[1..<10]))
        XCTAssertEqual(b.cells.map(\.position), Array(0..<9), "a click reports the row in the output")
        XCTAssertTrue(b.cells.allSatisfy { $0.showsNumber })
        XCTAssertEqual(b.reused, Array(repeating: true, count: 8) + [false], "eight cells stay, one enters")
        for i in 0..<8 { XCTAssertTrue(b.cells[i] === a.cells[i + 1], "row \(i) is the cell that was row \(i + 1)") }
        XCTAssertEqual(b.removed.count, 1)
        XCTAssertTrue(b.removed[0] === a.cells[0], "the row that scrolled out goes")
        // A move by two keeps the seven candidates both windows show; only the two that enter are new.
        let c = cells.update(candidates: Array(list[3..<12]), notes: none, selected: 0, first: 3, columns: 0, renumber: true)
        XCTAssertEqual(c.reused.filter { $0 }.count, 7, "the seven candidates both windows show keep their cells")
    }

    /// The bar and the grid keep their old rule: the number is part of the key, so the same text under another number is a
    /// new cell.
    @MainActor
    func testHorizontalReuseStillKeysOnTheNumber() {
        let cells = CandidateCells()
        let none = [String?](repeating: nil, count: 3)
        _ = cells.update(candidates: ["甲", "乙", "丙"], notes: none, selected: 0, first: 0, columns: 0)
        let b = cells.update(candidates: ["乙", "丙", "丁"], notes: none, selected: 0, first: 1, columns: 0)
        XCTAssertEqual(b.reused, [false, false, false])
    }

    @MainActor
    func testContentWidthIsWhatTheCellNeedsAndSurvivesAStretchedFrame() {
        let cell = CandidateCell(position: 0, numberText: "3", showsNumber: true, text: "善解", note: "名稱", selected: false)
        XCTAssertEqual(cell.contentWidth, CandidateCell.contentWidth(numberText: "3", text: "善解", note: "名稱"))
        XCTAssertEqual(cell.contentWidth, cell.frame.width)
        let before = cell.contentWidth
        cell.frame.size.width = 400  // the vertical window stretches it
        XCTAssertEqual(cell.contentWidth, before)
        cell.setNumber("7")
        XCTAssertEqual(cell.numberText, "7")
        XCTAssertEqual(cell.contentWidth, CandidateCell.contentWidth(numberText: "7", text: "善解", note: "名稱"))
    }

    // MARK: reset and size, contract section 2.4

    private func w(_ widths: [CGFloat]) -> [CGFloat] { widths }

    func testPlanWindowOverAPredictionRowStartsFromTheMinimum() {
        let row = VerticalLayout.plan(previous: nil, kind: 2, count: 3, contentWidths: [300, 120, 80], gutter: 0)
        XCTAssertTrue(row.reset, "first output after nothing")
        XCTAssertEqual(row.rows, 3)
        XCTAssertEqual(row.width, 300 + VerticalLayout.sideInset * 2)
        let window = VerticalLayout.plan(previous: row, kind: 1, count: 9, contentWidths: [100, 80], gutter: 9)
        XCTAssertTrue(window.reset, "2 -> 1 always resets")
        XCTAssertEqual(window.rows, 9)
        XCTAssertEqual(window.size.height, VerticalLayout.height(rows: 9))
        XCTAssertEqual(window.width, 226, "the minimum, not the row's width")
    }

    func testPlanRowAfterAWindowTakesItsOwnHeight() {
        let window = VerticalLayout.plan(previous: nil, kind: 1, count: 9, contentWidths: [260], gutter: 9)
        let row = VerticalLayout.plan(previous: window, kind: 2, count: 2, contentWidths: [50, 60], gutter: 0)
        XCTAssertTrue(row.reset, "1 -> 2 always resets")
        XCTAssertEqual(row.rows, 2)
        XCTAssertEqual(row.size.height, VerticalLayout.height(rows: 2))
        XCTAssertEqual(row.width, 226, "the window's width is not carried over")
    }

    func testPlanConsecutiveRowsFollowTheCountAndOnlyWiden() {
        let a = VerticalLayout.plan(previous: nil, kind: 2, count: 5, contentWidths: [250, 100], gutter: 0)
        let b = VerticalLayout.plan(previous: a, kind: 2, count: 3, contentWidths: [100, 90, 80], gutter: 0)
        XCTAssertFalse(b.reset)
        XCTAssertEqual(b.rows, 3, "the height follows the count on every output")
        XCTAssertEqual(b.size.height, VerticalLayout.height(rows: 3))
        XCTAssertEqual(b.width, a.width, "the width does not shrink")
        let c = VerticalLayout.plan(previous: b, kind: 2, count: 4, contentWidths: [400], gutter: 0)
        XCTAssertFalse(c.reset)
        XCTAssertEqual(c.rows, 4)
        XCTAssertEqual(c.width, 400 + VerticalLayout.sideInset * 2, "a wider row widens it")
        let d = VerticalLayout.plan(previous: c, kind: 2, count: 4, contentWidths: [60], gutter: 0)
        XCTAssertEqual(d.width, c.width)
    }

    func testPlanWindowKeepsItsHeightFromTheOpeningAndOnlyWidens() {
        let open = VerticalLayout.plan(previous: nil, kind: 1, count: 9, contentWidths: [100], gutter: 9)
        XCTAssertEqual(open.rows, 9)
        let scrolled = VerticalLayout.plan(previous: open, kind: 1, count: 9, contentWidths: [300], gutter: 9)
        XCTAssertFalse(scrolled.reset)
        XCTAssertEqual(scrolled.rows, 9)
        XCTAssertEqual(scrolled.width, 300 + VerticalLayout.sideInset * 2 + 9)
        let again = VerticalLayout.plan(previous: scrolled, kind: 1, count: 9, contentWidths: [100], gutter: 9)
        XCTAssertEqual(again.width, scrolled.width)
        // A short list: the height is its count, from the opening on.
        XCTAssertEqual(VerticalLayout.plan(previous: nil, kind: 1, count: 4, contentWidths: [100], gutter: 0).rows, 4)
    }

    func testPlanEveryChangeOfValueResetsAndSameValueDoesNot() {
        for from in 0...2 {
            for to in 0...2 {
                let previous = VerticalLayout.plan(previous: nil, kind: from, count: 4, contentWidths: [100], gutter: 0)
                let next = VerticalLayout.plan(previous: previous, kind: to, count: 4, contentWidths: [100], gutter: 0)
                XCTAssertEqual(next.reset, from != to, "\(from) -> \(to)")
            }
        }
        XCTAssertTrue(VerticalLayout.plan(previous: nil, kind: 0, count: 3, contentWidths: [], gutter: 0).reset, "nothing on screen before")
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
        // No line: the last origin is reused, but clamped for this size. A tall panel at a low last origin moves up; a wide
        // one at the right edge moves left; an origin that still fits stays.
        let kept = PanelPlacement.topLeft(lineRect: nil, lastOrigin: NSPoint(x: 100, y: 500), size: size, alignOffset: 10, screens: [screen], rectScreen: nil, main: 0)
        XCTAssertEqual(kept, NSPoint(x: 100, y: 500))
        let low2 = PanelPlacement.topLeft(lineRect: nil, lastOrigin: NSPoint(x: 100, y: 60), size: size, alignOffset: 10, screens: [screen], rectScreen: nil, main: 0)
        XCTAssertEqual(low2, NSPoint(x: 100, y: size.height), "the whole panel stays above the bottom edge")
        let edge = PanelPlacement.topLeft(lineRect: nil, lastOrigin: NSPoint(x: 1400, y: 500), size: size, alignOffset: 10, screens: [screen], rectScreen: nil, main: 0)
        XCTAssertEqual(edge, NSPoint(x: 1440 - 226, y: 500))
        let high = PanelPlacement.topLeft(lineRect: nil, lastOrigin: NSPoint(x: 100, y: 2000), size: size, alignOffset: 10, screens: [screen], rectScreen: nil, main: 0)
        XCTAssertEqual(high, NSPoint(x: 100, y: 900))
        // On a second screen it is clamped into that screen, not moved to the main one.
        let rightScreen = NSRect(x: 1440, y: 0, width: 1000, height: 800)
        let second = PanelPlacement.topLeft(lineRect: nil, lastOrigin: NSPoint(x: 2300, y: 60), size: size, alignOffset: 10, screens: [screen, rightScreen], rectScreen: nil, main: 0)
        XCTAssertEqual(second, NSPoint(x: 2440 - 226, y: size.height))
        // Clamped to the right edge by the panel's own width.
        let right = NSRect(x: 1400, y: 600, width: 8, height: 18)
        let clamped = PanelPlacement.topLeft(lineRect: right, lastOrigin: nil, size: size, alignOffset: 10, screens: [screen], rectScreen: 0, main: 0)
        XCTAssertEqual(clamped.x, 1440 - 226)
    }
}
