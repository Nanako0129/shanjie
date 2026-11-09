import XCTest
@testable import ShanjieKit

final class GridLayoutTests: XCTestCase {
    private func layout(_ cells: [CGFloat], columns: Int = 3, current: [CGFloat] = []) -> GridLayout.Result {
        GridLayout.layout(cellWidths: cells, columns: columns, current: current, inset: 3, spacing: 2, trailing: 12)
    }

    func testFirstRowWidestKeepsItsPositions() {
        let first = layout([40, 50, 60])
        let both = layout([40, 50, 60, 30, 20, 10])
        XCTAssertEqual(first.xs, [3, 45, 97])
        XCTAssertEqual(both.xs, first.xs)
        XCTAssertEqual(both.widths, [40, 50, 60])
        XCTAssertEqual(both.totalWidth, 3 + 40 + 2 + 50 + 2 + 60 + 12)
    }

    func testWiderLowerRowWidensItsColumnAndShiftsTheRest() {
        let r = layout([40, 50, 60, 70, 20, 10])
        XCTAssertEqual(r.widths, [70, 50, 60])
        XCTAssertEqual(r.xs, [3, 75, 127])
    }

    func testNarrowerContentKeepsWidths() {
        let wide = layout([40, 50, 60, 70, 20, 10])
        let later = layout([10, 10, 10], current: wide.widths)
        XCTAssertEqual(later, wide)
    }

    func testWiderCellOnlyWidens() {
        let before = layout([40, 50, 60])
        let after = layout([45, 10, 10], current: before.widths)
        XCTAssertEqual(after.widths, [45, 50, 60])
        XCTAssertEqual(after.xs, [3, 50, 102])
        XCTAssertGreaterThan(after.totalWidth, before.totalWidth)
    }

    func testShortFirstRowLeavesNoRoomForEmptyColumns() {
        let r = layout([40, 50], columns: 9)
        XCTAssertEqual(r.totalWidth, 3 + 40 + 2 + 50 + 12)
    }
}
