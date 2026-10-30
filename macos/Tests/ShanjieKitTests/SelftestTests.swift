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
        XCTAssertNil(Selftest.lastFailure)
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

    func testFailsWithoutTheModel() throws {
        let res = try XCTUnwrap(TestData.resources(only: ["mcbpmf-data.txt", "overlay-add.tsv", "sandhi-add.tsv", "names-add.tsv", "demote.tsv"]))
        XCTAssertNotEqual(Selftest.run(resources: res), 0)
    }

    /// S2k: the class file is part of the model; without it the model does not load (no silent fallback).
    func testFailsWithoutTheClasses() throws {
        let res = try XCTUnwrap(TestData.resources(only: ["mcbpmf-data.txt", "overlay-add.tsv", "sandhi-add.tsv", "names-add.tsv", "demote.tsv", "bigram.sjlm"]))
        XCTAssertNotEqual(Selftest.run(resources: res), 0)
    }

    /// trigram-core: the formal profile's table is read from beside the model. Without it the model still loads and formal gives 本市… on the
    /// selftest sentence instead of the expected 本是…, so a bundle without trigram.sjt3 must fail the selftest, at the sentence check (the load
    /// itself succeeds).
    func testFailsWithoutTheTrigramTable() throws {
        let res = try XCTUnwrap(TestData.resources(only: Set(TestData.files.map(\.name)).subtracting(["trigram.sjt3"])))
        try TestData.addBundledPack(to: res)
        XCTAssertNotEqual(Selftest.run(resources: res), 0)
        XCTAssertEqual(Selftest.lastFailure, "selftest: unexpected sentence for profile 1")
    }

    /// sj-lm-7-pr2.md: the chat profile's table is read from beside the model too. Without it chat gives 期中報告明天要教 on the second selftest
    /// sentence instead of 交, so a bundle without trigram-chat.sjt3 must fail the selftest (it would silently leave chat on the bigram), at the
    /// sentence check, not the load.
    func testFailsWithoutTheChatTrigramTable() throws {
        let res = try XCTUnwrap(TestData.resources(only: Set(TestData.files.map(\.name)).subtracting(["trigram-chat.sjt3"])))
        try TestData.addBundledPack(to: res)
        XCTAssertNotEqual(Selftest.run(resources: res), 0)
        XCTAssertEqual(Selftest.lastFailure, "selftest: unexpected sentence for profile 0")
    }

    /// A trigram table that is not the model's (corrupt, or a dangling symlink) is refused by the load itself, never ignored: the engine reads both
    /// tables, a bad one fails the whole load with code 3 (docs/contracts/sj-lm-7-pr2.md section 3 item 2). `lastFailure` tells that load failure
    /// (`shanjie_engine_load_lm failed, code 3`) from a wrong sentence, so a table that was silently skipped would fail this test.
    func testALoadRefusesABadTrigramTable() throws {
        for name in ["trigram.sjt3", "trigram-chat.sjt3"] {
            for dangling in [false, true] {
                let res = try XCTUnwrap(TestData.resources(only: Set(TestData.files.map(\.name)).subtracting([name])))
                try TestData.addBundledPack(to: res)
                let file = res.appendingPathComponent(name)
                if dangling {
                    try FileManager.default.createSymbolicLink(at: file, withDestinationURL: res.appendingPathComponent("missing.sjt3"))
                } else {
                    try Data("not a table".utf8).write(to: file)
                }
                XCTAssertNotEqual(Selftest.run(resources: res), 0, "\(name) dangling \(dangling)")
                XCTAssertEqual(Selftest.lastFailure, "selftest: shanjie_engine_load_lm failed, code 3", "\(name) dangling \(dangling)")
            }
        }
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
