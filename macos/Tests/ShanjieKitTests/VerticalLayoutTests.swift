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
    func testContentWidthSurvivesAStretchedFrameAndRenumbering() {
        let cell = CandidateCell(position: 0, numberText: "3", showsNumber: true, text: "善解", note: "名稱", selected: false)
        XCTAssertEqual(cell.contentWidth, cell.frame.width)
        let before = cell.contentWidth
        cell.frame.size.width = 400  // the vertical window stretches it
        XCTAssertEqual(cell.contentWidth, before)
        cell.setNumber("7")
        XCTAssertEqual(cell.numberText, "7")
        XCTAssertEqual(cell.contentWidth, CandidateCell(position: 0, numberText: "7", showsNumber: true, text: "善解", note: "名稱", selected: false).contentWidth)
    }

    /// The system font's digits are proportional, so a cell sized by its own number puts the candidate at a different x for
    /// each number. The vertical window's cells give the number a fixed slot: the candidate sits at the same x for 1-9 and a
    /// renumbering does not move it or change the width. The bar's and the grid's cells stay as they were.
    @MainActor
    func testVerticalCandidateXIsTheSameForNumbersOneToNine() {
        let numbers = (1...9).map(String.init)
        let vertical = numbers.map { CandidateCell(position: 0, numberText: $0, showsNumber: true, text: "字", note: nil, selected: false, fixedNumberSlot: true) }
        XCTAssertEqual(Set(vertical.map(\.candidateMinX)).count, 1, "one x for every number")
        XCTAssertEqual(Set(vertical.map(\.contentWidth)).count, 1)
        let widest = numbers.map { CandidateCell(position: 0, numberText: $0, showsNumber: true, text: "字", note: nil, selected: false).candidateMinX }.max()
        XCTAssertEqual(vertical[0].candidateMinX, widest, "the slot is the widest digit")
        let cell = vertical[0]
        let x = cell.candidateMinX
        for n in numbers { cell.setNumber(n); XCTAssertEqual(cell.candidateMinX, x, "renumbered to \(n)") }
        // Through CandidateCells.update the vertical rule gives the same result after a scroll.
        let cells = CandidateCells()
        let none = [String?](repeating: nil, count: 9)
        let list = (0..<12).map { "字\($0)" }
        _ = cells.update(candidates: Array(list[0..<9]), notes: none, selected: 0, first: 0, columns: 0, renumber: true)
        let scrolled = cells.update(candidates: Array(list[1..<10]), notes: none, selected: 0, first: 1, columns: 0, renumber: true)
        XCTAssertEqual(Set(scrolled.cells.map(\.candidateMinX)).count, 1)
        // The bar keeps each number's own width: its cells for "1" and "8" differ (the proportional digits).
        let bar = ["1", "8"].map { CandidateCell(position: 0, numberText: $0, showsNumber: true, text: "字", note: nil, selected: false) }
        XCTAssertNotEqual(bar[0].candidateMinX, bar[1].candidateMinX)
    }

    // MARK: reset and size, contract section 2.4

    func testPlanWindowOverAPredictionRowStartsFromTheMinimum() {
        let row = VerticalLayout.plan(previous: nil, kind: 2, count: 3, total: 3, contentWidths: [300, 120, 80])
        XCTAssertTrue(row.reset, "first output after nothing")
        XCTAssertEqual(row.rows, 3)
        XCTAssertEqual(row.width, 300 + VerticalLayout.sideInset * 2)
        let window = VerticalLayout.plan(previous: row, kind: 1, count: 9, total: 30, contentWidths: [100, 80])
        XCTAssertTrue(window.reset, "2 -> 1 always resets")
        XCTAssertEqual(window.rows, 9)
        XCTAssertEqual(window.size.height, VerticalLayout.height(rows: 9))
        XCTAssertEqual(window.width, 226, "the minimum, not the row's width")
    }

    func testPlanRowAfterAWindowTakesItsOwnHeight() {
        let window = VerticalLayout.plan(previous: nil, kind: 1, count: 9, total: 30, contentWidths: [260])
        let row = VerticalLayout.plan(previous: window, kind: 2, count: 2, total: 2, contentWidths: [50, 60])
        XCTAssertTrue(row.reset, "1 -> 2 always resets")
        XCTAssertEqual(row.rows, 2)
        XCTAssertEqual(row.size.height, VerticalLayout.height(rows: 2))
        XCTAssertEqual(row.width, 226, "the window's width is not carried over")
    }

    func testPlanConsecutiveRowsFollowTheCountAndOnlyWiden() {
        let a = VerticalLayout.plan(previous: nil, kind: 2, count: 5, total: 5, contentWidths: [250, 100])
        let b = VerticalLayout.plan(previous: a, kind: 2, count: 3, total: 3, contentWidths: [100, 90, 80])
        XCTAssertFalse(b.reset)
        XCTAssertEqual(b.rows, 3, "the height follows the count on every output")
        XCTAssertEqual(b.size.height, VerticalLayout.height(rows: 3))
        XCTAssertEqual(b.width, a.width, "the width does not shrink")
        let c = VerticalLayout.plan(previous: b, kind: 2, count: 4, total: 4, contentWidths: [400])
        XCTAssertFalse(c.reset)
        XCTAssertEqual(c.rows, 4)
        XCTAssertEqual(c.width, 400 + VerticalLayout.sideInset * 2, "a wider row widens it")
        let d = VerticalLayout.plan(previous: c, kind: 2, count: 4, total: 4, contentWidths: [60])
        XCTAssertEqual(d.width, c.width)
    }

    func testPlanWindowKeepsItsHeightFromTheOpeningAndOnlyWidens() {
        let open = VerticalLayout.plan(previous: nil, kind: 1, count: 9, total: 30, contentWidths: [100])
        XCTAssertEqual(open.rows, 9)
        let scrolled = VerticalLayout.plan(previous: open, kind: 1, count: 9, total: 30, contentWidths: [300])
        XCTAssertFalse(scrolled.reset)
        XCTAssertEqual(scrolled.rows, 9)
        XCTAssertEqual(scrolled.width, 300 + VerticalLayout.sideInset * 2 + 9)
        let again = VerticalLayout.plan(previous: scrolled, kind: 1, count: 9, total: 30, contentWidths: [100])
        XCTAssertEqual(again.width, scrolled.width)
        // A short list: the height is its count, from the opening on.
        XCTAssertEqual(VerticalLayout.plan(previous: nil, kind: 1, count: 4, total: 4, contentWidths: [100]).rows, 4)
    }

    /// The scroll gutter is decided once, in the plan: only the candidate window, only with a list longer than its rows.
    func testPlanGutterIsDecidedOnceInThePlan() {
        let long = VerticalLayout.plan(previous: nil, kind: 1, count: 9, total: 30, contentWidths: [300])
        XCTAssertEqual(long.gutter, VerticalLayout.scrollGutter)
        XCTAssertEqual(long.width, 300 + VerticalLayout.sideInset * 2 + VerticalLayout.scrollGutter)
        XCTAssertEqual(VerticalLayout.plan(previous: nil, kind: 1, count: 9, total: 9, contentWidths: [300]).gutter, 0, "the list fits")
        XCTAssertEqual(VerticalLayout.plan(previous: nil, kind: 2, count: 9, total: 30, contentWidths: [300]).gutter, 0, "a prediction row has none")
        XCTAssertEqual(VerticalLayout.plan(previous: nil, kind: 0, count: 9, total: 30, contentWidths: []).gutter, 0)
        XCTAssertTrue(VerticalLayout.resets(previous: nil, kind: 1))
        XCTAssertFalse(VerticalLayout.resets(previous: long, kind: 1))
        XCTAssertTrue(VerticalLayout.resets(previous: long, kind: 2))
    }

    func testPlanEveryChangeOfValueResetsAndSameValueDoesNot() {
        for from in 0...2 {
            for to in 0...2 {
                let previous = VerticalLayout.plan(previous: nil, kind: from, count: 4, total: 4, contentWidths: [100])
                let next = VerticalLayout.plan(previous: previous, kind: to, count: 4, total: 4, contentWidths: [100])
                XCTAssertEqual(next.reset, from != to, "\(from) -> \(to)")
            }
        }
        XCTAssertTrue(VerticalLayout.plan(previous: nil, kind: 0, count: 3, total: 3, contentWidths: []).reset, "nothing on screen before")
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

    // MARK: row separators (Apple Zhuyin's vertical window, measured 2026-10-10)

    private func seps(rows: Int = 9, selected: Int, rowWidth: CGFloat = 226) -> [VerticalLayout.Separator] {
        VerticalLayout.separators(rows: rows, selected: selected, rowWidth: rowWidth, capsuleHeight: 24, thickness: 1)
    }

    /// Apple's numbers: capsule tops 86, 114, ... and lines at 111, 139, ... (capsule top + 25), from 6 pt inside the
    /// capsule's left edge to 6 pt inside its right edge (capsule 11...237, line 17...231).
    func testSeparatorRectsFollowApple() {
        let s = seps(selected: -1)
        XCTAssertEqual(s.count, 8, "between nine rows, none above the first or below the last")
        for (i, line) in s.enumerated() {
            XCTAssertEqual(line.index, i)
            XCTAssertEqual(line.y, VerticalLayout.rowY(i, capsuleHeight: 24) + 25, "line \(i)")
            XCTAssertEqual(line.height, 1)
        }
        XCTAssertEqual(s[1].y - s[0].y, 28, "one pitch apart")
        // Capsule at x = sideInset with a width of rowWidth: the line starts 6 in and ends 6 before the capsule's end.
        XCTAssertEqual(s[0].x, VerticalLayout.sideInset + 6)
        XCTAssertEqual(s[0].x + s[0].width, VerticalLayout.sideInset + 226 - 6)
        XCTAssertEqual(seps(selected: -1, rowWidth: 300)[0].width, 288)
        // Thickness is whatever the caller says (1 device pixel).
        XCTAssertEqual(VerticalLayout.separators(rows: 3, selected: -1, rowWidth: 226, capsuleHeight: 24, thickness: 0.5)[0].height, 0.5)
        // Apple's check at its own origin: with the first capsule top at 86 the line is at 111.
        XCTAssertEqual(86 + VerticalLayout.separatorBelowCapsuleTop, 111)
    }

    func testSeparatorsTouchingTheSelectedRowAreHidden() {
        func hidden(_ selected: Int, rows: Int = 9) -> [Int] { seps(rows: rows, selected: selected).filter { !$0.visible }.map(\.index) }
        XCTAssertEqual(hidden(0), [0], "row 1 selected: no line between rows 1 and 2, the one between 2 and 3 shows")
        XCTAssertEqual(hidden(1), [0, 1], "row 2 selected: neither 1|2 nor 2|3")
        XCTAssertEqual(hidden(4), [3, 4], "a middle row")
        XCTAssertEqual(hidden(8), [7], "the last row: only the line above it")
        XCTAssertEqual(hidden(-1), [], "no selection (the not-entered prediction row): all show")
        XCTAssertEqual(hidden(1, rows: 3), [0, 1])
        XCTAssertEqual(hidden(2, rows: 3), [1])
    }

    func testSeparatorsOfShortListsAndOneRow() {
        XCTAssertEqual(seps(rows: 1, selected: 0).count, 0)
        XCTAssertEqual(seps(rows: 0, selected: -1).count, 0)
        XCTAssertEqual(seps(rows: 2, selected: -1).count, 1)
        XCTAssertEqual(seps(rows: 2, selected: 0).filter(\.visible).count, 0)
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
        // The remembered place is the anchor, not the clamped result: after a tall panel was clamped, a short one asks for the
        // original anchor again and gets it.
        let anchor = NSPoint(x: 100, y: 60)
        let tall = PanelPlacement.topLeft(lineRect: nil, lastOrigin: anchor, size: size, alignOffset: 10, screens: [screen], rectScreen: nil, main: 0)
        XCTAssertEqual(tall.y, size.height, "the tall panel was raised")
        let kept2 = PanelPlacement.anchor(afterShowingAt: tall, lineRect: nil, lastOrigin: anchor)
        XCTAssertEqual(kept2, anchor, "the anchor is not replaced by the clamped origin")
        let shortSize = NSSize(width: 100, height: 28)
        let shortBar = PanelPlacement.topLeft(lineRect: nil, lastOrigin: kept2, size: shortSize, alignOffset: 10, screens: [screen], rectScreen: nil, main: 0)
        XCTAssertEqual(shortBar, anchor, "the short bar returns to the original anchor")
        // With a line the origin is remembered; with nothing remembered, too.
        XCTAssertEqual(PanelPlacement.anchor(afterShowingAt: tall, lineRect: NSRect(x: 1, y: 2, width: 3, height: 4), lastOrigin: anchor), tall)
        XCTAssertEqual(PanelPlacement.anchor(afterShowingAt: tall, lineRect: nil, lastOrigin: nil), tall)
        // Clamped to the right edge by the panel's own width.
        let right = NSRect(x: 1400, y: 600, width: 8, height: 18)
        let clamped = PanelPlacement.topLeft(lineRect: right, lastOrigin: nil, size: size, alignOffset: 10, screens: [screen], rectScreen: 0, main: 0)
        XCTAssertEqual(clamped.x, 1440 - 226)
    }
}
