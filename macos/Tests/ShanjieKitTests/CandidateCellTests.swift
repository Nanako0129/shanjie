import AppKit
import XCTest
@testable import ShanjieKit

/// docs/contracts/s3b2-glass-panel.md section 9.1: the drawn cells, their reuse when the grid scrolls,
/// and the per-key cost against the NSTextField cells they replaced.
@MainActor
final class CandidateCellTests: XCTestCase {
    private let columns = 9
    private func list(_ n: Int) -> (texts: [String], notes: [String?]) {
        let texts = (0..<n).map { i in ["你", "好", "世界", "輸入", "善解", "春天", "日", "月", "星辰"][i % 9] + (i >= 9 ? String(UnicodeScalar(0x4E00 + i)!) : "") }
        return (texts, texts.map { _ in nil })
    }
    /// The output of a grid whose first visible row is page `page` of a long list: 5 rows of 9.
    private func grid(_ cells: CandidateCells, page: Int, selected: Int) -> CandidateCells.Update {
        let all = list(200)
        let range = (page * columns)..<(page * columns + 45)
        return cells.update(candidates: Array(all.texts[range]), notes: Array(all.notes[range]),
                            selected: selected, first: page * columns, columns: columns)
    }

    func testScrollingOneRowReusesThirtySixCells() {
        let cells = CandidateCells()
        let a = grid(cells, page: 0, selected: 0)
        XCTAssertEqual(a.reused.filter { !$0 }.count, 45)
        let b = grid(cells, page: 1, selected: 9)
        XCTAssertEqual(b.reused.filter { $0 }.count, 36)
        XCTAssertEqual(b.reused.filter { !$0 }.count, 9)
        XCTAssertEqual(b.removed.count, 9)
        // The removed ones are the old first row.
        XCTAssertEqual(Set(b.removed.map(ObjectIdentifier.init)), Set(a.cells[0..<9].map(ObjectIdentifier.init)))
    }

    func testClickAfterScrollReportsPositionInTheNewOutputAndOneSelection() {
        let cells = CandidateCells()
        var clicked: [Int] = []
        cells.onSelect = { clicked.append($0) }
        _ = grid(cells, page: 0, selected: 11)
        let b = grid(cells, page: 1, selected: 2)  // the third cell of the new first row was old position 11
        b.cells[2].click()
        XCTAssertEqual(clicked, [2])
        XCTAssertEqual(b.cells.filter(\.isSelected).count, 1)
        XCTAssertTrue(b.cells[2].isSelected)
        // Number shows only on the selected row.
        XCTAssertEqual(b.cells.enumerated().filter { $0.element.showsNumber }.map(\.offset), Array(0..<9))
    }

    /// A selection move inside the visible rows (no scroll) keeps every cell and moves the number row
    /// with the selection; the same holds for the collapsed bar within a page. From the 2026-10-05 verifier.
    func testSelectionMoveInsideVisibleRowsReusesEveryCell() {
        let cells = CandidateCells()
        let a = grid(cells, page: 2, selected: 0)
        for sel in [9, 20, 44, 3] {
            let b = grid(cells, page: 2, selected: sel)
            XCTAssertEqual(b.reused, Array(repeating: true, count: 45), "sel \(sel)")
            XCTAssertTrue(b.removed.isEmpty, "sel \(sel)")
            XCTAssertEqual(b.cells.map(ObjectIdentifier.init), a.cells.map(ObjectIdentifier.init), "sel \(sel)")
            XCTAssertEqual(b.cells.indices.filter { b.cells[$0].isSelected }, [sel])
            XCTAssertEqual(b.cells.indices.filter { b.cells[$0].showsNumber }, Array((sel / 9 * 9)..<(sel / 9 * 9 + 9)))
        }
        let bar = CandidateCells()
        let page = list(9)
        let x = bar.update(candidates: page.texts, notes: page.notes, selected: 0, first: 0, columns: 0)
        let y = bar.update(candidates: page.texts, notes: page.notes, selected: 5, first: 0, columns: 0)
        XCTAssertEqual(y.reused, Array(repeating: true, count: 9))
        XCTAssertEqual(x.cells.map(ObjectIdentifier.init), y.cells.map(ObjectIdentifier.init))
    }

    func testChangingModeRebuildsEverything() {
        let cells = CandidateCells()
        let all = list(9)
        let bar = cells.update(candidates: all.texts, notes: all.notes, selected: 0, first: 0, columns: 0)
        let g = grid(cells, page: 0, selected: 0)
        XCTAssertEqual(g.reused.filter { $0 }.count, 0)
        XCTAssertEqual(g.removed.count, bar.cells.count)
    }

    func testIgnoredMouseTakesNoClick() {
        let cells = CandidateCells()
        var clicked = 0
        cells.onSelect = { _ in clicked += 1 }
        let a = grid(cells, page: 0, selected: 0)
        a.cells[0].ignoresMouse = true
        a.cells[0].click()
        XCTAssertEqual(clicked, 0)
        XCTAssertNil(a.cells[0].hitTest(NSPoint(x: 5, y: 5)))
    }

    private func cell(_ text: String, note: String? = nil, showsNumber: Bool = true) -> CandidateCell {
        CandidateCell(position: 0, numberText: "1", showsNumber: showsNumber, text: text, note: note, selected: false)
    }

    func testCellWidths() {
        XCTAssertEqual(cell("你", showsNumber: false).frame.width, cell("你").frame.width)
        XCTAssertGreaterThan(cell("你好").frame.width, cell("你").frame.width)
        XCTAssertGreaterThan(cell("，", note: "全形逗號").frame.width, cell("，").frame.width)
        XCTAssertGreaterThan(cell("😀").frame.width, 0)
        XCTAssertEqual(cell("你").frame.height, CellMetrics.capsuleHeight)
    }

    // MARK: Old cells (NSTextField, 4b22c30) for the comparisons below

    /// The cell as it was before section 9.1, kept here only to compare against.
    private final class LegacyCell: NSView {
        init(number: String, text: String, note: String?, selected: Bool) {
            func label(_ s: String, _ font: NSFont, _ color: NSColor) -> NSTextField {
                let t = NSTextField(labelWithString: s)
                t.font = font
                t.textColor = selected ? .white : color
                t.sizeToFit()
                return t
            }
            let n = label(number, CellMetrics.numberFont, .secondaryLabelColor)
            let c = label(text, CellMetrics.candidateFont, .labelColor)
            let m = note.map { label($0, CellMetrics.nameFont, .secondaryLabelColor) }
            var width = 2 + n.frame.width + 2 + c.frame.width
            if let m { width += 2 + m.frame.width }
            width += 5
            super.init(frame: NSRect(x: 0, y: 0, width: width, height: CellMetrics.capsuleHeight))
            var x: CGFloat = 2
            for v in [n, c] + (m.map { [$0] } ?? []) {
                v.setFrameOrigin(NSPoint(x: x, y: ((CellMetrics.capsuleHeight - v.frame.height) / 2).rounded()))
                addSubview(v)
                x += v.frame.width + 2
            }
        }
        required init?(coder: NSCoder) { fatalError() }
    }

    /// The ink's bounding box (pixels, alpha above 30) of a view rendered in dark mode.
    private func inkBox(_ v: NSView) -> [Int] {
        v.appearance = NSAppearance(named: .darkAqua)
        let rep = v.bitmapImageRepForCachingDisplay(in: v.bounds)!
        v.cacheDisplay(in: v.bounds, to: rep)
        var box = [Int.max, 0, Int.max, 0]
        for y in 0..<rep.pixelsHigh { for x in 0..<rep.pixelsWide where rep.colorAt(x: x, y: y)!.alphaComponent > 30.0 / 255 {
            box = [min(box[0], x), max(box[1], x), min(box[2], y), max(box[3], y)]
        } }
        return box
    }

    /// Metrics claim: the drawn cell has the old cell's width and puts its ink where the old one did
    /// (within 1 pixel; widths round to the highest screen scale, as the old text fields do, so this holds on a 1x CI display and on a 2x screen). Stroke weight is not compared: `cacheDisplay`
    /// of the old text fields came out heavier than a plain draw, which looks like a capture artefact
    /// (unverified); main compares on-device screenshots.
    func testWidthsAndInkMatchTheOldCells() {
        for (text, note) in [("你", nil), ("你好", nil), ("，", "全形逗號"), ("善解輸入法", nil), ("😀", nil)] as [(String, String?)] {
            let new = CandidateCell(position: 0, numberText: "1", showsNumber: true, text: text, note: note, selected: false)
            let old = LegacyCell(number: "1", text: text, note: note, selected: false)
            XCTAssertEqual(new.frame.width, old.frame.width, accuracy: 0.01, text)
            for (a, b) in zip(inkBox(new), inkBox(old)) { XCTAssertEqual(a, b, accuracy: 1, text) }
        }
    }

    // MARK: Benchmark (not a pass gate)

    private func stats(_ ms: [Double]) -> String {
        let s = ms.sorted()
        func p(_ q: Double) -> Double { s[min(s.count - 1, Int(Double(s.count) * q))] }
        return String(format: "p50 %.2f ms  p95 %.2f ms  max %.2f ms", p(0.5), p(0.95), s.last!)
    }
    private func load() -> Double { var l = [Double](repeating: 0, count: 1); getloadavg(&l, 1); return l[0] }
    private func time(_ body: () -> Void) -> Double {
        let t = ContinuousClock.now
        body()
        let d = ContinuousClock.now - t
        return Double(d.components.seconds) * 1000 + Double(d.components.attoseconds) / 1e15
    }

    /// Pre-release benchmark (contract 9.1); a measurement, not a check, so it runs only with `SHANJIE_BENCH=1 swift test -c release --package-path macos --filter testBenchmark`.
    func testBenchmark() throws {
        try XCTSkipUnless(ProcessInfo.processInfo.environment["SHANJIE_BENCH"] == "1", "set SHANJIE_BENCH=1 to run")
        let rounds = 200
        let all = list(400)
        let host = NSView(frame: NSRect(x: 0, y: 0, width: 600, height: 200))
        func output(_ first: Int) -> (texts: [String], notes: [String?]) {
            (Array(all.texts[first..<first + 45]), Array(all.notes[first..<first + 45]))
        }
        // The adapter's part: GridLayout, add/remove the reported views, set every origin.
        func apply(_ u: CandidateCells.Update) {
            u.removed.forEach { $0.removeFromSuperview() }
            let lay = GridLayout.layout(cellWidths: u.cells.map(\.frame.width), columns: 9, current: [], inset: 3, spacing: 2, trailing: 12)
            for (i, c) in u.cells.enumerated() {
                c.setFrameOrigin(NSPoint(x: lay.xs[i % 9], y: CGFloat(i / 9) * 28))
                if !u.reused[i] { host.addSubview(c) }
            }
        }
        var scroll: [Double] = [], expand: [Double] = [], oldScroll: [Double] = [], oldExpand: [Double] = []
        let loadBefore = load()
        for r in 0..<rounds {
            let cells = CandidateCells()
            let bar = cells.update(candidates: Array(all.texts[0..<9]), notes: Array(all.notes[0..<9]), selected: 0, first: 0, columns: 0)
            host.subviews.forEach { $0.removeFromSuperview() }
            apply(bar)
            let o = output(0)
            expand.append(time { apply(cells.update(candidates: o.texts, notes: o.notes, selected: 0, first: 0, columns: 9)) })
            let n = output(9 * (1 + r % 20))
            // Scroll one row: the previous output is shifted by one row.
            let p = output(9 * (r % 20))
            _ = cells.update(candidates: p.texts, notes: p.notes, selected: 0, first: 9 * (r % 20), columns: 9)
            scroll.append(time { apply(cells.update(candidates: n.texts, notes: n.notes, selected: 9, first: 9 * (1 + r % 20), columns: 9)) })
            oldExpand.append(time { host.subviews.forEach { $0.removeFromSuperview() }
                for (i, t) in o.texts.enumerated() { host.addSubview(LegacyCell(number: String(i % 9 + 1), text: t, note: nil, selected: i == 0)) } })
            oldScroll.append(time { host.subviews.forEach { $0.removeFromSuperview() }
                for (i, t) in n.texts.enumerated() { host.addSubview(LegacyCell(number: String(i % 9 + 1), text: t, note: nil, selected: i == 9)) } })
        }
        print("BENCH load before \(loadBefore) after \(load()) (200 rounds, 45 cells)")
        print("BENCH new   one-row scroll:  \(stats(scroll))")
        print("BENCH new   45-cell expand:  \(stats(expand))")
        print("BENCH old   one-row scroll (rebuild 45 NSTextField cells): \(stats(oldScroll))")
        print("BENCH old   45-cell expand:  \(stats(oldExpand))")
    }
}
