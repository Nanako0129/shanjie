import XCTest
@testable import ShanjieKit

/// Acceptance 6 (docs/contracts/s3b.md section 9): the selftest passes only with the engine, the
/// LM and both expected sentences. The built binary's argument handling and side effects are
/// checked by scripts/check-app.sh.
@MainActor
final class SelftestTests: XCTestCase {
    func testPassesWithTheFullResources() throws {
        XCTAssertEqual(Selftest.run(resources: try XCTUnwrap(TestData.resources())), 0)
    }

    func testFailsWithoutTheModel() throws {
        let res = try XCTUnwrap(TestData.resources(only: ["mcbpmf-data.txt", "overlay-add.tsv", "sandhi-add.tsv"]))
        XCTAssertNotEqual(Selftest.run(resources: res), 0)
    }

    func testFailsWithoutTheLexicon() throws {
        let res = try XCTUnwrap(TestData.resources(only: ["overlay-add.tsv", "bigram.sjlm"]))
        XCTAssertNotEqual(Selftest.run(resources: res), 0)
    }

    func testFailsWithAModelThatIsNotOne() throws {
        let res = try XCTUnwrap(TestData.resources(only: ["mcbpmf-data.txt", "overlay-add.tsv", "sandhi-add.tsv"]))
        try Data("not a model".utf8).write(to: res.appendingPathComponent("bigram.sjlm"))
        XCTAssertNotEqual(Selftest.run(resources: res), 0)
    }
}
