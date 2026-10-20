import Foundation
import Security
import ShanjieInstall
import XCTest

// docs/contracts/system-install.md section 2.3 and 4.1: the root stage's command, run as the current
// user with /bin/sh against a temporary root it owns (owner = this user). Nothing here asks for a
// password, runs anything inside a bundle or touches /Library: the test bundles are only signed,
// copied and read.
final class RootStageTests: XCTestCase {
    private var tmp: URL!
    private var root: String { tmp.appendingPathComponent("Library/Input Methods").path }
    private var dest: String { root + "/善解輸入法.app" }
    private let shipping = "com.nyanako.inputmethod.shanjie"
    private var identifierRequirement: String { "identifier \"\(shipping)\"" }

    override func setUpWithError() throws {
        // A space in the root, as /Library/Input Methods has.
        tmp = FileManager.default.temporaryDirectory.appendingPathComponent("RootStageTests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: tmp.appendingPathComponent("Library"), withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        _ = sh("/usr/bin/chflags", ["-R", "nouchg", tmp.path])
        _ = sh("/bin/chmod", ["-R", "u+w", tmp.path])
        try? FileManager.default.removeItem(at: tmp)
    }

    @discardableResult
    private func sh(_ tool: String, _ args: [String]) -> (status: Int32, err: String) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: tool)
        p.arguments = args
        let pipe = Pipe()
        p.standardError = pipe
        p.standardOutput = FileHandle.nullDevice
        try! p.run()
        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return (p.terminationStatus, String(decoding: data, as: UTF8.self))
    }

    /// A minimal bundle in `<folder>/善解輸入法.app`, ad-hoc signed (its executable is a script that
    /// is never run). Returns the folder, the root stage's source.
    private func bundle(_ name: String, id: String? = nil, version: String = "0.5.0", marker: String? = nil, sign: Bool = true,
                        links: [String: String] = [:]) throws -> String {
        let folder = tmp.appendingPathComponent("src-\(name)")
        let app = folder.appendingPathComponent("善解輸入法.app")
        let contents = app.appendingPathComponent("Contents")
        try FileManager.default.createDirectory(at: contents.appendingPathComponent("MacOS"), withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: contents.appendingPathComponent("Resources"), withIntermediateDirectories: true)
        let exe = contents.appendingPathComponent("MacOS/shanjie")
        try "#!/bin/sh\nexit 1\n".write(to: exe, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: exe.path)
        try (marker ?? name).write(to: contents.appendingPathComponent("Resources/marker"), atomically: true, encoding: .utf8)
        let plist: [String: Any] = ["CFBundleIdentifier": id ?? shipping, "CFBundleExecutable": "shanjie",
                                    "CFBundleVersion": version, "CFBundleShortVersionString": version, "CFBundlePackageType": "APPL"]
        try PropertyListSerialization.data(fromPropertyList: plist, format: .xml, options: 0).write(to: contents.appendingPathComponent("Info.plist"))
        // Symbolic links in Contents/Resources (name -> destination), part of the signed bundle.
        for (link, destination) in links {
            try FileManager.default.createSymbolicLink(atPath: contents.appendingPathComponent("Resources/\(link)").path,
                                                       withDestinationPath: destination)
        }
        if sign { XCTAssertEqual(sh("/usr/bin/codesign", ["--sign", "-", app.path]).status, 0) }
        return folder.path
    }

    /// An installed copy already at the destination (not signed: the root stage only reads its version).
    private func installed(version: String, marker: String) throws {
        let src = try bundle("installed-\(marker)", version: version, marker: marker, sign: false)
        try FileManager.default.createDirectory(atPath: root, withIntermediateDirectories: true)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: root)
        try FileManager.default.moveItem(atPath: src + "/善解輸入法.app", toPath: dest)
    }

    private func marker(_ app: String) -> String? {
        try? String(contentsOfFile: app + "/Contents/Resources/marker", encoding: .utf8)
    }

    private func params(requirement: String? = nil, version: String = "0.5.0") -> RootStage.Parameters {
        RootStage.Parameters(root: root, ownerUID: getuid(), ownerGID: getgid(), requirement: requirement ?? identifierRequirement, version: version)
    }

    private func runStage(_ source: String, _ p: RootStage.Parameters) throws -> (status: Int32, err: String) {
        sh("/bin/sh", ["-c", try RootStage.command(source: source, p)])
    }

    private func noStaging(file: StaticString = #filePath, line: UInt = #line) {
        let names = (try? FileManager.default.contentsOfDirectory(atPath: root)) ?? []
        XCTAssertFalse(names.contains { $0.hasPrefix(".shanjie-staging.") }, "staging left: \(names)", file: file, line: line)
    }

    private func hasACL(_ path: String) -> Bool {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/ls")
        p.arguments = ["-leR", path]
        let pipe = Pipe()
        p.standardOutput = pipe
        try! p.run()
        let out = String(decoding: pipe.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        p.waitUntilExit()
        return out.split(separator: "\n").contains { $0.range(of: #"^ *[0-9]+: "#, options: .regularExpression) != nil }
    }

    // MARK: success

    func testInstallsIntoAFreshRootWithCleanModes() throws {
        let src = try bundle("v1")
        // A group-writable, setuid file in the source must not keep either bit.
        let res = src + "/善解輸入法.app/Contents/Resources/marker"
        XCTAssertEqual(sh("/bin/chmod", ["6775", res]).status, 0)
        let r = try runStage(src, params())
        XCTAssertEqual(r.status, 0, r.err)
        XCTAssertEqual(marker(dest), "v1")
        let rootAttrs = try FileManager.default.attributesOfItem(atPath: root)
        XCTAssertEqual(rootAttrs[.posixPermissions] as? Int, 0o755, "created 755")
        XCTAssertEqual((rootAttrs[.ownerAccountID] as? NSNumber)?.uint32Value, getuid())
        let walker = FileManager.default.enumerator(atPath: dest)!
        for case let relative as String in walker {
            let attrs = try FileManager.default.attributesOfItem(atPath: dest + "/" + relative)
            let mode = attrs[.posixPermissions] as! Int
            XCTAssertEqual(mode & 0o022, 0, "\(relative) is group or other writable")
            XCTAssertEqual(mode & 0o6000, 0, "\(relative) keeps setuid or setgid")
            XCTAssertEqual(mode & 0o044, 0o044, "\(relative) is not readable by all")
        }
        XCTAssertFalse(FileManager.default.fileExists(atPath: root + "/.shanjie-previous"))
        noStaging()
    }

    func testReinstallSameVersionKeepsThePrevious() throws {
        try installed(version: "0.5.0", marker: "old")
        try FileManager.default.createDirectory(atPath: root + "/.shanjie-previous", withIntermediateDirectories: true)
        let r = try runStage(try bundle("new"), params())
        XCTAssertEqual(r.status, 0, r.err)
        XCTAssertEqual(marker(dest), "new")
        XCTAssertEqual(marker(root + "/.shanjie-previous"), "old", "the installed copy became the previous version")
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: root).sorted(), [".shanjie-previous", "善解輸入法.app"])
        noStaging()
    }

    /// Code review: the rename of the installed copy fails (here it is immutable, `chflags uchg`):
    /// the kept previous version, already set aside in the staging folder, is put back, and the
    /// installed copy and the destination stay as they were.
    func testAFailedRenameKeepsThePreviousVersion() throws {
        try installed(version: "0.4.9", marker: "installed")
        let previous = try bundle("kept", version: "0.4.0", marker: "kept", sign: false) + "/善解輸入法.app"
        try FileManager.default.moveItem(atPath: previous, toPath: root + "/.shanjie-previous")
        XCTAssertEqual(sh("/usr/bin/chflags", ["uchg", dest]).status, 0)
        let r = try runStage(try bundle("new"), params())
        XCTAssertNotEqual(r.status, 0)
        XCTAssertTrue(r.err.contains("cannot keep the previous version"), r.err)
        XCTAssertEqual(marker(dest), "installed")
        XCTAssertEqual(marker(root + "/.shanjie-previous"), "kept", "the previous version was lost")
        noStaging()
    }

    /// Code review: the staging folder keeps mktemp's 0700 until it is removed; owner and modes
    /// are applied to the copy inside it only. The run cannot be paused mid-way, so this reads the
    /// built script: no chown or chmod names the staging folder itself.
    func testTheStagingFolderIsNeverOpenedUp() throws {
        let command = try RootStage.command(source: "/tmp/x", params())
        let script = try XCTUnwrap(decodeShellSingleQuoted(String(command.dropFirst(RootStage.commandPrefix.count))))
        let lines = script.split(separator: "\n").filter { $0.contains("/bin/chmod") || $0.contains("/usr/sbin/chown") }
        XCTAssertFalse(lines.isEmpty)
        for line in lines {
            XCTAssertFalse(line.contains("\"$S\" ") || line.hasSuffix("\"$S\""), "touches the staging folder: \(line)")
        }
    }

    func testUpdateFromAnOlderInstalledVersion() throws {
        try installed(version: "0.4.9", marker: "old")
        let r = try runStage(try bundle("new"), params())
        XCTAssertEqual(r.status, 0, r.err)
        XCTAssertEqual(marker(dest), "new")
        XCTAssertEqual(marker(root + "/.shanjie-previous"), "old")
    }

    /// Security review P1-1: an ACL on the source (here added before the copy; in the attack, while
    /// the password dialog is up) is not carried into the root-owned copy. Measured 2026-10-20 on
    /// macOS 27.0.1: `ditto --noacl` alone still copied this ACL, so `chmod -RN` is what removes it.
    func testAnACLOnTheSourceDoesNotReachTheCopy() throws {
        let src = try bundle("acl")
        let app = src + "/善解輸入法.app"
        XCTAssertEqual(sh("/bin/chmod", ["+a", "everyone allow write,delete", app + "/Contents/Resources/marker"]).status, 0)
        XCTAssertEqual(sh("/bin/chmod", ["+a", "everyone allow add_file,delete_child", app + "/Contents"]).status, 0)
        XCTAssertTrue(hasACL(app), "the test did not set an ACL")
        let r = try runStage(src, params())
        XCTAssertEqual(r.status, 0, r.err)
        XCTAssertFalse(hasACL(dest), "the copy carries an ACL")
    }

    /// Verifier finding F1 (caseG): links in the copy to an outside file and an outside folder that
    /// carry ACLs. Nothing may follow them before the signature check: the outside ACLs survive,
    /// under the production requirement and under the identifier requirement (the ad-hoc signature
    /// is valid, but `--strict` refuses links that point out of the bundle, so a bundle with such
    /// links never gets past step 5).
    func testLinksOutOfTheCopyAreNeverFollowed() throws {
        let outsideFile = tmp.appendingPathComponent("victimfile").path
        let outsideDir = tmp.appendingPathComponent("victimdir").path
        try FileManager.default.createDirectory(atPath: outsideDir, withIntermediateDirectories: true)
        try "v".write(toFile: outsideFile, atomically: true, encoding: .utf8)
        try "v".write(toFile: outsideDir + "/file", atomically: true, encoding: .utf8)
        XCTAssertEqual(sh("/bin/chmod", ["+a", "everyone deny delete", outsideFile]).status, 0)
        XCTAssertEqual(sh("/bin/chmod", ["+a", "everyone deny delete_child", outsideDir]).status, 0)
        XCTAssertEqual(sh("/bin/chmod", ["+a", "everyone deny write", outsideDir + "/file"]).status, 0)
        let src = try bundle("links", links: ["l1": outsideFile, "l2": outsideDir])
        for requirement in [RootStage.requirement, identifierRequirement] {
            let r = try runStage(src, params(requirement: requirement))
            XCTAssertNotEqual(r.status, 0, "accepted a copy with links out of the bundle")
            XCTAssertTrue(r.err.contains("signing requirement"), r.err)
            for path in [outsideFile, outsideDir, outsideDir + "/file"] {
                XCTAssertTrue(hasACL(path), "root stage removed the ACL of \(path) through a link")
            }
            XCTAssertNil(marker(dest))
            noStaging()
        }
    }

    /// A link inside the bundle is kept as a link, and its target's ACL is removed on the target
    /// itself (the ACL step skips links but still covers every real file).
    func testALinkInsideTheCopyIsKeptAndItsTargetCleaned() throws {
        let src = try bundle("inner", links: ["alias": "marker"])
        XCTAssertEqual(sh("/bin/chmod", ["+a", "everyone allow write,delete", src + "/善解輸入法.app/Contents/Resources/marker"]).status, 0)
        let r = try runStage(src, params())
        XCTAssertEqual(r.status, 0, r.err)
        XCTAssertEqual(try FileManager.default.destinationOfSymbolicLink(atPath: dest + "/Contents/Resources/alias"), "marker")
        XCTAssertFalse(hasACL(dest), "the copy carries an ACL")
    }

    // MARK: refusals: the destination is unchanged and no staging is left

    private func assertRefused(_ source: String, _ p: RootStage.Parameters, expect: String, keeps installedMarker: String?,
                               file: StaticString = #filePath, line: UInt = #line) throws {
        let r = try runStage(source, p)
        XCTAssertNotEqual(r.status, 0, "not refused", file: file, line: line)
        XCTAssertTrue(r.err.contains(expect), "refused for another reason: \(r.err)", file: file, line: line)
        XCTAssertEqual(marker(dest), installedMarker, "the destination changed", file: file, line: line)
        noStaging(file: file, line: line)
    }

    func testTheProductionRequirementRefusesAnAdHocCopy() throws {
        try installed(version: "0.4.9", marker: "old")
        try assertRefused(try bundle("adhoc"), params(requirement: RootStage.requirement), expect: "signing requirement", keeps: "old")
        XCTAssertFalse(FileManager.default.fileExists(atPath: root + "/.shanjie-previous"))
    }

    func testTheDevelopmentIDIsRefused() throws {
        try installed(version: "0.4.9", marker: "old")
        try assertRefused(try bundle("dev", id: shipping + ".dev"), params(), expect: "signing requirement", keeps: "old")
    }

    func testAnUnsignedCopyIsRefused() throws {
        try assertRefused(try bundle("unsigned", sign: false), params(), expect: "signing requirement", keeps: nil)
    }

    func testAWritableParentStops() throws {
        try installed(version: "0.4.9", marker: "old")
        let src = try bundle("v1")
        for mode in [0o775, 0o757, 0o777] {
            try FileManager.default.setAttributes([.posixPermissions: mode], ofItemAtPath: root)
            try assertRefused(src, params(), expect: "writable by its group or others", keeps: "old")
        }
    }

    func testAParentOwnedBySomeoneElseStops() throws {
        try installed(version: "0.4.9", marker: "old")
        var p = params()
        p.ownerUID = getuid() + 1
        try assertRefused(try bundle("v1"), p, expect: "is not owned by uid", keeps: "old")
    }

    func testASymlinkedParentStops() throws {
        let real = tmp.appendingPathComponent("elsewhere").path
        try FileManager.default.createDirectory(atPath: real, withIntermediateDirectories: true)
        try FileManager.default.createSymbolicLink(atPath: root, withDestinationPath: real)
        let r = try runStage(try bundle("v1"), params())
        XCTAssertNotEqual(r.status, 0)
        XCTAssertTrue(r.err.contains("is not a directory"), r.err)
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: real), [])
    }

    func testAVersionBelowTheConstantIsRefused() throws {
        try installed(version: "0.4.0", marker: "old")
        try assertRefused(try bundle("old-signed", version: "0.4.9"), params(version: "0.5.0"), expect: "not 0.5.0", keeps: "old")
    }

    func testAVersionAboveTheConstantIsRefused() throws {
        try assertRefused(try bundle("newer", version: "0.6.0"), params(version: "0.5.0"), expect: "not 0.5.0", keeps: nil)
    }

    func testADowngradeOfTheInstalledCopyIsRefused() throws {
        try installed(version: "0.10.0", marker: "newer")  // numeric: 0.10.0 > 0.5.0
        try assertRefused(try bundle("v1"), params(), expect: "newer than 0.5.0", keeps: "newer")
        XCTAssertFalse(FileManager.default.fileExists(atPath: root + "/.shanjie-previous"))
    }

    func testASymlinkedDestinationIsRefused() throws {
        try FileManager.default.createDirectory(atPath: root, withIntermediateDirectories: true)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: root)
        let target = tmp.appendingPathComponent("target").path
        try FileManager.default.createDirectory(atPath: target, withIntermediateDirectories: true)
        try FileManager.default.createSymbolicLink(atPath: dest, withDestinationPath: target)
        let r = try runStage(try bundle("v1"), params())
        XCTAssertNotEqual(r.status, 0)
        XCTAssertTrue(r.err.contains("is a symbolic link"), r.err)
        XCTAssertEqual(try FileManager.default.destinationOfSymbolicLink(atPath: dest), target)
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: target), [])
        noStaging()
    }

    // MARK: building the command

    func testUnexpectedCharactersInThePathAreRefused() {
        for bad in ["relative/x", "/tmp/a'b", "/tmp/a\"b", "/tmp/a\\b", "/tmp/$(id)", "/tmp/`id`", "/tmp/a\nb", "/tmp/a;b",
                    "/tmp/../etc", "/tmp/./x", "/tmp/a*b", ""] {
            XCTAssertThrowsError(try RootStage.command(source: bad, params()), bad) {
                XCTAssertEqual($0 as? RootStage.BuildError, .unexpectedPath)
            }
        }
        XCTAssertNoThrow(try RootStage.command(source: NSTemporaryDirectory() + "shanjie-installer-\(UUID().uuidString)", params()))
    }

    func testAMissingOrMalformedVersionIsRefused() {
        for bad in ["", "x", "0.5.0'", "0..5", ".5.0", "0.5.", "0.5.0 "] {
            XCTAssertThrowsError(try RootStage.command(source: "/tmp/x", params(version: bad)), bad)
        }
    }

    func testTheProductionParameters() {
        let p = RootStage.Parameters.production(version: "0.5.0")
        XCTAssertEqual(p.root, "/Library/Input Methods")
        XCTAssertEqual(p.ownerUID, 0)
        XCTAssertEqual(p.ownerGID, 0)
        XCTAssertEqual(p.requirement,
                       #"anchor apple generic and identifier "com.nyanako.inputmethod.shanjie" and certificate 1[field.1.2.840.113635.100.6.2.6] exists and certificate leaf[field.1.2.840.113635.100.6.1.13] exists and certificate leaf[subject.OU] = "2LJ882GPY8""#)
    }

    /// Security review P3-5: the AppleScript source decodes back to exactly the command, the command
    /// to exactly the script, and the script's SRC line to exactly the path, with a space and
    /// full-width characters in the path. The AppleScript layer is decoded twice: by hand, and by
    /// AppleScript itself evaluating only the string literal (`return "…"`, no shell).
    func testTheAppleScriptSourceRoundTrips() throws {
        let path = "/private/var/folders/x y/善解 測試/shanjie-installer-\(UUID().uuidString)"
        let p = RootStage.Parameters.production(version: "0.5.0")
        let source = try RootStage.appleScript(source: path, p)
        let head = "do shell script ", tail = " with administrator privileges"
        XCTAssertTrue(source.hasPrefix(head) && source.hasSuffix(tail))
        let literal = String(source.dropFirst(head.count).dropLast(tail.count))

        let command = try XCTUnwrap(decodeAppleScriptLiteral(literal))
        XCTAssertEqual(command, try RootStage.command(source: path, p))
        var error: NSDictionary?
        let evaluated = NSAppleScript(source: "return " + literal)?.executeAndReturnError(&error).stringValue
        XCTAssertNil(error)
        XCTAssertEqual(evaluated, command, "AppleScript reads the literal differently")

        XCTAssertTrue(command.hasPrefix("/usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin /bin/sh -c '"))
        let script = try XCTUnwrap(decodeShellSingleQuoted(String(command.dropFirst(RootStage.commandPrefix.count))))
        let srcLine = try XCTUnwrap(script.split(separator: "\n").first { $0.hasPrefix("SRC=") })
        XCTAssertEqual(decodeShellSingleQuoted(String(srcLine.dropFirst(4))), path)
        let reqLine = try XCTUnwrap(script.split(separator: "\n").first { $0.hasPrefix("REQ=") })
        XCTAssertEqual(decodeShellSingleQuoted(String(reqLine.dropFirst(4))), RootStage.requirement)
        // The script has no other variable part: rebuilt with another path it differs only in SRC.
        let other = try XCTUnwrap(decodeShellSingleQuoted(String(try RootStage.command(source: "/tmp/other", p).dropFirst(RootStage.commandPrefix.count))))
        XCTAssertEqual(script.split(separator: "\n").filter { !$0.hasPrefix("SRC=") },
                       other.split(separator: "\n").filter { !$0.hasPrefix("SRC=") })
    }

    /// `"…"` with `\\` and `\"` escapes; nil on anything else.
    private func decodeAppleScriptLiteral(_ s: String) -> String? {
        guard s.count >= 2, s.first == "\"", s.last == "\"" else { return nil }
        var out = "", escaped = false
        for c in s.dropFirst().dropLast() {
            if escaped {
                guard c == "\\" || c == "\"" else { return nil }
                out.append(c)
                escaped = false
            } else if c == "\\" {
                escaped = true
            } else if c == "\"" {
                return nil
            } else {
                out.append(c)
            }
        }
        return escaped ? nil : out
    }

    /// One word made of `'…'` and `\'` pieces; nil on anything else.
    private func decodeShellSingleQuoted(_ s: String) -> String? {
        var out = "", rest = Substring(s)
        while !rest.isEmpty {
            if rest.hasPrefix("\\'") {
                out.append("'")
                rest = rest.dropFirst(2)
            } else if rest.first == "'" {
                guard let end = rest.dropFirst().firstIndex(of: "'") else { return nil }
                out += rest[rest.index(after: rest.startIndex)..<end]
                rest = rest[rest.index(after: end)...]
            } else {
                return nil
            }
        }
        return out
    }

    // MARK: the in-process check before the password dialog

    /// The production requirement compiles: otherwise every "refused under the production
    /// requirement" case above would pass for the wrong reason.
    func testTheProductionRequirementParses() {
        var req: SecRequirement?
        XCTAssertEqual(SecRequirementCreateWithString(RootStage.requirement as CFString, [], &req), errSecSuccess)
        XCTAssertNotNil(req)
        var text: CFString?
        XCTAssertEqual(SecRequirementCopyString(req!, [], &text), errSecSuccess)
        XCTAssertTrue((text as String?)?.contains("1.2.840.113635.100.6.1.13") ?? false, "\(String(describing: text))")
    }

    func testThePrecheckUsesTheSameRequirement() throws {
        let app = URL(fileURLWithPath: try bundle("pre") + "/善解輸入法.app")
        XCTAssertTrue(RootStage.satisfies(bundle: app, requirement: identifierRequirement))
        XCTAssertFalse(RootStage.satisfies(bundle: app, requirement: RootStage.requirement))
        let dev = URL(fileURLWithPath: try bundle("pre-dev", id: shipping + ".dev") + "/善解輸入法.app")
        XCTAssertFalse(RootStage.satisfies(bundle: dev, requirement: identifierRequirement))
        XCTAssertFalse(RootStage.satisfies(bundle: tmp.appendingPathComponent("missing.app"), requirement: identifierRequirement))
    }
}
