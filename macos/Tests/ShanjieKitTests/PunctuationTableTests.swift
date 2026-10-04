import XCTest
@testable import ShanjieKit

/// docs/contracts/s3e-punctuation-candidates.md acceptance 3, through the real C core.
@MainActor
final class PunctuationTableTests: XCTestCase {
    /// A fixture written here (not Apple's file): what is kept and what is skipped.
    func testConversionSkipsWhatTheCoreWouldReject() throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("punct-\(UUID().uuidString).plist")
        defer { try? FileManager.default.removeItem(at: url) }
        let fixture: [String: Any] = [
            "，": ["、", "《"],
            "：": ["；", 7],            // the non-string element is dropped
            "。": ["a\tb"],             // a tab inside an alternative: skipped whole
            "、": ["＼\r\nx"],          // a CRLF (one Swift Character) holds a line feed: skipped whole
            "；": ["\r\n"],             // an alternative that is exactly a CRLF: skipped whole
            "「」": ["『"],             // two scalars: skipped
            "？": [String](),          // no alternatives: skipped
            "！": "not an array",       // skipped
        ]
        try PropertyListSerialization.data(fromPropertyList: fixture, format: .xml, options: 0).write(to: url)
        XCTAssertEqual(PunctuationTable.load(from: url), "，\t、\t《\n：\t；\n")

        let empty = FileManager.default.temporaryDirectory.appendingPathComponent("punct-empty-\(UUID().uuidString).plist")
        defer { try? FileManager.default.removeItem(at: empty) }
        try PropertyListSerialization.data(fromPropertyList: ["。": ["a\nb"]], format: .xml, options: 0).write(to: empty)
        XCTAssertNil(PunctuationTable.load(from: empty), "nothing valid is nil")
        XCTAssertNil(PunctuationTable.load(from: url.appendingPathExtension("missing")))
    }

    private func comma(_ shell: Shell) -> [String] {
        let c = Controller(shell)
        c.session.activate()
        c.press(Keys.code(for: ","), flags: .shift)
        c.press(Keys.space)
        return c.panel.items
    }

    /// The user's real system table, converted and taken by the real core. Fails (never skips)
    /// when the file is missing: that is the "feature silently does nothing" case to catch.
    func testSystemTableReachesTheCore() throws {
        let path = PunctuationTable.systemURL.path
        XCTAssertTrue(FileManager.default.fileExists(atPath: path), "the system punctuation table is missing: \(path)")
        let table = try XCTUnwrap(PunctuationTable.load(from: PunctuationTable.systemURL), "the system table converted to nothing")
        let resources = try XCTUnwrap(TestData.resources())
        try {   // scoped: this engine is freed before the Shell builds its own (one ~240 MB engine at a time)
            let (engine, code) = CoreEngine.make(dataDir: resources.path, layout: 0)
            XCTAssertEqual(code, 0)
            XCTAssertEqual(try XCTUnwrap(engine).setPunctuation(table), 0, "the core rejected the converted system table")
        }()
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore())
        let items = comma(shell)
        XCTAssertEqual(items.first, "，")
        XCTAssertTrue(items.contains("、"), "the system table's alternatives for ， did not reach the candidates")
    }

    /// No system table: the core's built-in list (s3e section 3).
    func testMissingTableFallsBackToTheBuiltInList() throws {
        let resources = try XCTUnwrap(TestData.resources())
        let missing = FileManager.default.temporaryDirectory.appendingPathComponent("no-such-\(UUID().uuidString).plist")
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false },
                          layoutStore: MemoryLayoutStore(), punctuationTable: missing)
        XCTAssertEqual(comma(shell), ["，", "〈", "《", "︿", "︽"])
    }
}
