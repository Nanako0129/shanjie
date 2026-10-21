import XCTest
@testable import ShanjieKit

/// docs/contracts/about-models.md acceptance 3.
final class ModelListTests: XCTestCase {
    private let sha = String(repeating: "0", count: 64)

    func testSixRowsInFileOrder() throws {
        let names = [("bigram.sjlm", "通語", "SJ-LM-6-9.1M"), ("classes.sjc", "辨類", "SJ-WC-5-512C"), ("kn.sjkn", "承接", "SJ-KN-1-353K"),
                     ("trigram.sjt3", "連文", "SJ-CX-1-7.7M"), ("tinylm.sjtl", "會意", "SJ-SR-6-14M"), ("colloquial.sjlm", "常談", "SJ-CL-1-68K")]
        let text = names.map { "\($0.0)\t\(sha)\t\($0.1)\t\($0.2)\n" }.joined()
        let rows = ModelList.parse(text)
        XCTAssertEqual(rows.map(\.name), names.map { $0.1 })
        XCTAssertEqual(rows.map(\.code), names.map { $0.2 })
        XCTAssertEqual(rows.map(\.file), names.map { $0.0 })
        // The same text without the final newline reads the same.
        XCTAssertEqual(ModelList.parse(String(text.dropLast())), rows)
    }

    func testEmptyFileHasNoRows() {
        XCTAssertEqual(ModelList.parse(""), [])
    }

    func testRowWithTheWrongFieldCountMakesTheFileUnreadable() {
        XCTAssertEqual(ModelList.parse("a.sjlm\t\(sha)\t通語\tSJ-LM-6-9.1M\nb.sjc\t\(sha)\t辨類\n"), [])
        XCTAssertEqual(ModelList.parse("a.sjlm\t\(sha)\t通語\tSJ-LM-6-9.1M\textra\n"), [])
    }

    func testMissingFileHasNoRows() {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("models-\(UUID().uuidString).tsv")
        XCTAssertEqual(ModelList.load(from: url), [])
        XCTAssertEqual(ModelList.load(from: nil), [])
    }
}
