import XCTest
@testable import ShanjieKit

/// Acceptance 6 (docs/contracts/s3b.md section 9): the selftest passes only with the engine, the
/// LM and both expected sentences. The built binary's argument handling and side effects are
/// checked by scripts/check-app.sh.
@MainActor
final class SelftestTests: XCTestCase {
    func testPassesWithTheFullResources() throws {
        let res = try XCTUnwrap(TestData.resources())
        try TestData.addBundledPack(to: res)
        XCTAssertEqual(Selftest.run(resources: res), 0)
    }

    /// The pack ships on by default, so a bundle without it (or with a broken one) must fail the selftest.
    func testFailsWithoutTheBundledPack() throws {
        XCTAssertNotEqual(Selftest.run(resources: try XCTUnwrap(TestData.resources())), 0)
    }

    /// it-pack contract 3.2: the IT+AI pack ships on by default too, so a bundle without it-add.tsv, or with a broken one, fails the selftest.
    func testFailsWithoutTheBundledItPack() throws {
        let res = try XCTUnwrap(TestData.resources())
        try TestData.addBundledPack(to: res, itPack: false)
        XCTAssertNotEqual(Selftest.run(resources: res), 0)
    }

    func testFailsWithACorruptBundledItPack() throws {
        let res = try XCTUnwrap(TestData.resources())
        try TestData.addBundledPack(to: res)
        try "not a row\n".write(to: res.appendingPathComponent("packs/it-add.tsv"), atomically: true, encoding: .utf8)
        XCTAssertNotEqual(Selftest.run(resources: res), 0)
    }

    /// meme-pack contract 3.2: the meme pack ships on by default too (and the selftest types one of its words), so a bundle without meme-add.tsv,
    /// or with a broken one, fails. The IT pack is present in both, so only the meme file can be the cause.
    func testFailsWithoutTheBundledMemePack() throws {
        let res = try XCTUnwrap(TestData.resources())
        try TestData.addBundledPack(to: res, memePack: false)
        XCTAssertNotEqual(Selftest.run(resources: res), 0)
    }

    /// The word comes from the file's first data row (comment lines skipped), not from a constant: a bundle whose first row is another word still passes.
    func testReadsTheMemeWordFromTheFirstDataRow() throws {
        let res = try XCTUnwrap(TestData.resources())
        try TestData.addBundledPack(to: res)
        let file = res.appendingPathComponent("packs/meme-add.tsv")
        XCTAssertEqual(Selftest.run(resources: res), 0)
        // The core's pack parser takes no comment lines, so this is the sampler alone: it would skip them (and blank lines) anyway.
        try ("# a comment line\n\n" + TestData.memePackRow).write(to: file, atomically: true, encoding: .utf8)
        XCTAssertEqual(Selftest.memeSample(res)?.word, "佛心來的")
        XCTAssertEqual(Selftest.memeSample(res)?.keys, "zj6vup x962k7", "the keys derived from the reading are the ones typed by hand before")
        try "ㄅㄚ-ㄅㄚ\t巴巴\t-6.0\tmeme\n".write(to: file, atomically: true, encoding: .utf8)
        XCTAssertEqual(Selftest.memeSample(res)?.word, "巴巴")
        XCTAssertEqual(Selftest.memeSample(res)?.keys, "18 18 ", "a toneless syllable ends with a space")
    }

    func testFailsWithACorruptBundledMemePack() throws {
        let res = try XCTUnwrap(TestData.resources())
        try TestData.addBundledPack(to: res)
        try "not a row\n".write(to: res.appendingPathComponent("packs/meme-add.tsv"), atomically: true, encoding: .utf8)
        XCTAssertNotEqual(Selftest.run(resources: res), 0)
    }

    func testFailsWithACorruptBundledPack() throws {
        let res = try XCTUnwrap(TestData.resources())
        let packs = res.appendingPathComponent("packs")
        try FileManager.default.createDirectory(at: packs, withIntermediateDirectories: true)
        try "not a row\n".write(to: packs.appendingPathComponent("acg-add.tsv"), atomically: true, encoding: .utf8)
        XCTAssertNotEqual(Selftest.run(resources: res), 0)
    }

    /// s7c2b section 2.5: the switch can be turned on, and without the model it would silently do nothing, so a bundle without it
    /// must fail the selftest (the switch ships on by default since S7h, so such a bundle would silently do nothing for most users).
    func testFailsWithoutTheSmallModel() throws {
        let res = try XCTUnwrap(TestData.resources(only: ["mcbpmf-data.txt", "overlay-add.tsv", "sandhi-add.tsv", "names-add.tsv", "demote.tsv", "bigram.sjlm", "classes.sjc"]))
        try TestData.addBundledPack(to: res)
        XCTAssertNotEqual(Selftest.run(resources: res), 0)
    }

    func testFailsWithACorruptSmallModel() throws {
        let res = try XCTUnwrap(TestData.resources(only: ["mcbpmf-data.txt", "overlay-add.tsv", "sandhi-add.tsv", "names-add.tsv", "demote.tsv", "bigram.sjlm", "classes.sjc"]))
        try TestData.addBundledPack(to: res)
        try Data("not a model".utf8).write(to: res.appendingPathComponent("tinylm.sjtl"))
        XCTAssertNotEqual(Selftest.run(resources: res), 0)
    }

    /// colloquial-term.md section 2.3: the table ships in the bundle and the term is part of the chat rerank, so a bundle without it, or with
    /// one the core refuses, must fail the selftest.
    func testFailsWithoutTheColloquialTable() throws {
        let res = try XCTUnwrap(TestData.resources(only: Set(TestData.files.map(\.name)).subtracting(["colloquial.sjlm"])))
        try TestData.addBundledPack(to: res)
        XCTAssertNotEqual(Selftest.run(resources: res), 0)
    }

    func testFailsWithACorruptColloquialTable() throws {
        let res = try XCTUnwrap(TestData.resources(only: Set(TestData.files.map(\.name)).subtracting(["colloquial.sjlm"])))
        try TestData.addBundledPack(to: res)
        try Data("not a table".utf8).write(to: res.appendingPathComponent("colloquial.sjlm"))
        XCTAssertNotEqual(Selftest.run(resources: res), 0)
    }

    /// zhen-mixed section 3.2: the word list ships in the bundle and the switch can be turned on, so a bundle without it, with a list
    /// the core refuses, or with one where `rust` Space opens no English window must fail the selftest.
    func testFailsWithoutTheEnglishList() throws {
        let res = try XCTUnwrap(TestData.resources(only: Set(TestData.files.map(\.name)).subtracting(["english-words.txt"])))
        try TestData.addBundledPack(to: res)
        XCTAssertNotEqual(Selftest.run(resources: res), 0)
    }

    func testFailsWithARefusedEnglishList() throws {
        let res = try XCTUnwrap(TestData.resources())
        try TestData.addBundledPack(to: res)
        try "AB\n".write(to: res.appendingPathComponent("english-words.txt"), atomically: true, encoding: .utf8)
        XCTAssertNotEqual(Selftest.run(resources: res), 0)
    }

    func testFailsWhenTheEnglishWindowDoesNotOpen() throws {
        let res = try XCTUnwrap(TestData.resources())
        try TestData.addBundledPack(to: res)
        try "google\nhello\n".write(to: res.appendingPathComponent("english-words.txt"), atomically: true, encoding: .utf8)
        XCTAssertNotEqual(Selftest.run(resources: res), 0)
    }

    func testFailsWithoutTheModel() throws {
        let res = try XCTUnwrap(TestData.resources(only: ["mcbpmf-data.txt", "overlay-add.tsv", "sandhi-add.tsv", "names-add.tsv", "demote.tsv"]))
        XCTAssertNotEqual(Selftest.run(resources: res), 0)
    }

    /// S2k: the class file is part of the model; without it the model does not load (no silent fallback).
    func testFailsWithoutTheClasses() throws {
        let res = try XCTUnwrap(TestData.resources(only: ["mcbpmf-data.txt", "overlay-add.tsv", "sandhi-add.tsv", "names-add.tsv", "demote.tsv", "bigram.sjlm"]))
        XCTAssertNotEqual(Selftest.run(resources: res), 0)
    }

    /// trigram-core: the formal profile's table is read from beside the model. Without it the model still loads and formal gives 濾水壺 on the
    /// selftest sentence instead of the expected 律水壺, so a bundle without trigram.sjt3 must fail the selftest.
    func testFailsWithoutTheTrigramTable() throws {
        let res = try XCTUnwrap(TestData.resources(only: Set(TestData.files.map(\.name)).subtracting(["trigram.sjt3"])))
        try TestData.addBundledPack(to: res)
        XCTAssertNotEqual(Selftest.run(resources: res), 0)
    }

    func testFailsWithoutTheLexicon() throws {
        let res = try XCTUnwrap(TestData.resources(only: ["overlay-add.tsv", "bigram.sjlm", "classes.sjc"]))
        XCTAssertNotEqual(Selftest.run(resources: res), 0)
    }

    func testFailsWithAModelThatIsNotOne() throws {
        let res = try XCTUnwrap(TestData.resources(only: ["mcbpmf-data.txt", "overlay-add.tsv", "sandhi-add.tsv", "names-add.tsv", "demote.tsv"]))
        try Data("not a model".utf8).write(to: res.appendingPathComponent("bigram.sjlm"))
        XCTAssertNotEqual(Selftest.run(resources: res), 0)
    }
}
