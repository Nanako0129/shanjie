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
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(), learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore())
        let items = comma(shell)
        XCTAssertEqual(items.first, "，")
        XCTAssertTrue(items.contains("、"), "the system table's alternatives for ， did not reach the candidates")
    }

    // MARK: s3f names

    func testNamesSkipWhatCannotBeShown() throws {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("names-\(UUID().uuidString).strings")
        defer { try? FileManager.default.removeItem(at: url) }
        let fixture: [String: Any] = [
            "，": "全形逗號",
            "、": "頓號",
            "。": "",                       // empty name: skipped
            "；": "a\tb",                  // tab: skipped
            "：": String(repeating: "長", count: 17),  // over maxName: skipped
            "！": 7,                        // not a string: skipped
            "": "空",                       // empty key: skipped
        ]
        try PropertyListSerialization.data(fromPropertyList: fixture, format: .binary, options: 0).write(to: url)
        XCTAssertEqual(PunctuationNames.load(from: url), ["，": "全形逗號", "、": "頓號"])
        XCTAssertEqual(PunctuationNames.load(from: url.appendingPathExtension("missing")), [:])
    }

    /// The user's real names table reaches the panel beside the real candidates; word candidates
    /// get none. Fails (never skips) when the file is missing.
    func testSystemNamesReachThePanel() throws {
        let path = PunctuationNames.systemURL.path
        XCTAssertTrue(FileManager.default.fileExists(atPath: path), "the system punctuation names are missing: \(path)")
        let resources = try XCTUnwrap(TestData.resources())
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(), learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore())
        let c = Controller(shell)
        c.session.activate()
        c.press(Keys.code(for: ","), flags: .shift)
        c.press(Keys.space)
        let named = Dictionary(zip(c.panel.items, c.panel.notes), uniquingKeysWith: { a, _ in a })
        XCTAssertEqual(named["，"], "全形逗號")
        XCTAssertEqual(named["、"], "頓號")
        XCTAssertEqual(c.panel.notes.count, c.panel.items.count)

        // The panel got the name; a click on the second cell chooses the mark without it (2 → 、).
        let want = c.panel.items[1]
        XCTAssertEqual(c.panel.notes[1], "頓號", "the second cell has a name")
        c.panel.click(1)
        XCTAssertFalse(c.panel.visible)
        XCTAssertEqual(c.client.marked, want, "the click chose the candidate, never its name")
    }

    func testWordCandidatesAndMissingNamesShowNoNames() throws {
        let resources = try XCTUnwrap(TestData.resources())
        let missing = FileManager.default.temporaryDirectory.appendingPathComponent("no-names-\(UUID().uuidString).strings")
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false },
                          layoutStore: MemoryLayoutStore(), learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), punctuationNames: missing)
        let c = Controller(shell)
        c.session.activate()
        c.press(Keys.code(for: ","), flags: .shift)
        c.press(Keys.space)
        XCTAssertFalse(c.panel.items.isEmpty)
        XCTAssertTrue(c.panel.notes.allSatisfy { $0 == nil }, "no names table, no names")

        let words = Controller(Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(), learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore()))
        words.session.activate()
        words.type("su3")   // ㄋㄧˇ
        words.press(Keys.space)
        XCTAssertGreaterThan(words.panel.items.count, 1)
        XCTAssertTrue(words.panel.notes.allSatisfy { $0 == nil }, "word candidates have no names")
    }

    /// No system table: the core's built-in list (s3e section 3).
    func testMissingTableFallsBackToTheBuiltInList() throws {
        let resources = try XCTUnwrap(TestData.resources())
        let missing = FileManager.default.temporaryDirectory.appendingPathComponent("no-such-\(UUID().uuidString).plist")
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false },
                          layoutStore: MemoryLayoutStore(), learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(), punctuationTable: missing)
        XCTAssertEqual(comma(shell), ["，", "〈", "《", "︿", "︽"])
    }
}
