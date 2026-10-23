import XCTest
@testable import ShanjieKit

/// docs/contracts/profile-choice.md section 3.1: how chat or formal is chosen, the per-app menu switch, the stores read back at
/// start, the composition owner, and the settings window's model. Observable results are row 284 typed then Enter, as in
/// `ShellTests.testProfileFollowsTheApp`.
@MainActor
final class ProfileChoiceTests: XCTestCase {
    private var resources: URL!
    private static let textEdit = "com.apple.TextEdit", discord = "com.hnc.Discord", notes = "com.apple.Notes"

    override func setUp() async throws {
        resources = try XCTUnwrap(TestData.resources())
    }

    private func makeShell(mode: MemoryProfileModeStore = MemoryProfileModeStore(),
                           apps: MemoryAppProfileStore = MemoryAppProfileStore()) -> Shell {
        let shell = Shell(resources: resources, panel: FakePanel(), isSecureInput: { false }, layoutStore: MemoryLayoutStore(),
                          learningDirectory: nil, dialogs: FakeDialogs(), demoteStore: MemoryDemoteStore(), predictionStore: MemoryPredictionStore(),
                          abbreviationStore: MemoryAbbreviationStore(), englishStore: MemoryEnglishStore(), acgPackStore: MemoryAcgPackStore(), glassTintStore: MemoryGlassTintStore(),
                          candidateOrientationStore: MemoryCandidateOrientationStore(), rerankStore: MemoryRerankStore(), rerankRunner: HeldRerankRunner(), profileModeStore: mode, appProfileStore: apps, spacingStore: MemorySpacingStore())
        XCTAssertNotNil(shell.engine)
        return shell
    }

    /// Activates, types row 284 and Enter; returns what the client received in this call. Without a left context, so an
    /// earlier row in the same client cannot change the result (as `ShellTests.testOwnerChangeReappliesTheProfile`).
    private func row284(_ c: Controller) -> String {
        let before = c.client.text
        c.client.selectedOverride = NSRange(location: NSNotFound, length: 0)
        c.session.activate()
        c.type(Row284.standardKeys)
        c.press(Keys.enter)
        return String(c.client.text.dropFirst(before.count))
    }

    private func item(_ c: Controller) -> MenuEntry? { c.session.menu.first { $0.action == .toggleAppChat } }

    private func stored(_ store: MemoryAppProfileStore) -> [String: String]? { store.appProfiles as? [String: String] }

    // MARK: Shell.profile (section 2.2)

    func testProfileTable() {
        let chat = Shell.chat, formal = Shell.formal
        let overrides: [String: UInt32] = [Self.textEdit: chat, Self.discord: formal]
        // (bundle, overrides) -> expected in auto
        let cases: [(String?, [String: UInt32], UInt32, String)] = [
            (Self.discord, [:], chat, "built-in chat app"),
            (Self.textEdit, [:], formal, "other app"),
            (nil, overrides, formal, "nil"),
            ("", ["": chat], formal, "empty string"),
            (Self.textEdit, overrides, chat, "other app set to chat"),
            (Self.discord, overrides, formal, "built-in chat app set to formal"),
        ]
        for (bundle, o, auto, label) in cases {
            XCTAssertEqual(Shell.profile(bundle: bundle, mode: .auto, overrides: o), auto, "auto, \(label)")
            XCTAssertEqual(Shell.profile(bundle: bundle, mode: .chat, overrides: o), chat, "chat, \(label)")
            XCTAssertEqual(Shell.profile(bundle: bundle, mode: .formal, overrides: o), formal, "formal, \(label)")
        }
    }

    // MARK: observable results

    func testMenuSwitchChangesTextEditToChatAndDiscordToFormal() {
        let shell = makeShell()
        let textEdit = Controller(shell, bundle: Self.textEdit), discord = Controller(shell, bundle: Self.discord)
        XCTAssertEqual(row284(textEdit), Row284.formal)
        XCTAssertEqual(row284(discord), Row284.chat)
        textEdit.session.activate()
        textEdit.session.perform(.toggleAppChat)
        XCTAssertEqual(row284(textEdit), Row284.chat)
        discord.session.activate()
        discord.session.perform(.toggleAppChat)
        XCTAssertEqual(row284(discord), Row284.formal)
    }

    /// The two forced modes give their profile in every app, the built-in chat app and another alike; auto restores the rule.
    func testForcedModes() {
        let shell = makeShell()
        let textEdit = Controller(shell, bundle: Self.textEdit), discord = Controller(shell, bundle: Self.discord)
        shell.setProfileMode(.chat)
        XCTAssertEqual(row284(textEdit), Row284.chat)
        XCTAssertEqual(row284(discord), Row284.chat)
        shell.setProfileMode(.formal)
        XCTAssertEqual(row284(discord), Row284.formal)
        XCTAssertEqual(row284(textEdit), Row284.formal)
        shell.setProfileMode(.auto)
        XCTAssertEqual(row284(discord), Row284.chat)
    }

    /// Section 1: mid-composition, the switch recomputes the composition at once; Enter commits the new profile's result
    /// without any new activateServer.
    func testSwitchMidCompositionRecomputesAtOnce() {
        let c = Controller(makeShell(), bundle: Self.textEdit)
        c.session.activate()
        c.type(Row284.standardKeys)
        XCTAssertEqual(c.client.marked, Row284.formal)
        c.session.perform(.toggleAppChat)
        XCTAssertEqual(c.client.marked, Row284.chat, "the composition was not recomputed")
        XCTAssertEqual(c.client.text, "", "nothing committed by the switch")
        c.press(Keys.enter)
        XCTAssertEqual(c.client.text, Row284.chat)
    }

    // MARK: menu (section 2.3)

    func testMenuItemPositionCheckAndAvailability() {
        let shell = makeShell()
        let textEdit = Controller(shell, bundle: Self.textEdit), discord = Controller(shell, bundle: Self.discord)
        let titles = textEdit.session.menu.map(\.title)
        let at = titles.firstIndex(of: "這個 App 用聊天設定")
        XCTAssertEqual(at.map { titles[$0 - 1] }, "已開啟的詞包：動漫與遊戲詞、資訊與 AI 詞")
        XCTAssertEqual(at.map { titles[$0 + 1] }, "善解設定…")
        XCTAssertEqual(item(textEdit)?.checked, false)
        XCTAssertEqual(item(textEdit)?.enabled, true)
        XCTAssertEqual(item(discord)?.checked, true)
        textEdit.session.perform(.toggleAppChat)
        XCTAssertEqual(item(textEdit)?.checked, true, "the checkmark follows the result")
        discord.session.perform(.toggleAppChat)
        XCTAssertEqual(item(discord)?.checked, false)

        shell.setProfileMode(.formal)
        XCTAssertEqual(item(textEdit)?.enabled, false, "greyed outside auto")
        XCTAssertEqual(item(textEdit)?.checked, false, "checked as forced")
        shell.setProfileMode(.chat)
        XCTAssertEqual(item(discord)?.enabled, false)
        XCTAssertEqual(item(discord)?.checked, true)
        let before = shell.appProfiles
        discord.session.perform(.toggleAppChat)
        XCTAssertEqual(shell.appProfiles, before, "a greyed item changes nothing")

        for bundle in [nil, ""] as [String?] {
            let none = Controller(shell, bundle: bundle)
            XCTAssertNil(item(none), "no bundle ID: no item")
            XCTAssertTrue(none.session.menu.contains { $0.title == "善解設定…" })
        }
    }

    func testPressingTwiceLeavesNoEntry() {
        let apps = MemoryAppProfileStore()
        let shell = makeShell(apps: apps)
        let textEdit = Controller(shell, bundle: Self.textEdit), discord = Controller(shell, bundle: Self.discord)
        textEdit.session.perform(.toggleAppChat)
        XCTAssertEqual(stored(apps), [Self.textEdit: "chat"])
        textEdit.session.perform(.toggleAppChat)
        XCTAssertNil(stored(apps)?[Self.textEdit], "same as the built-in rule: removed, not stored")
        discord.session.perform(.toggleAppChat)
        XCTAssertEqual(stored(apps), [Self.discord: "formal"])
        discord.session.perform(.toggleAppChat)
        XCTAssertNil(apps.appProfiles, "an empty list removes the value")
    }

    // MARK: stores (section 2.1)

    func testUnsetOrUnknownProfileModeIsAuto() {
        for value in [nil, "", "Chat", "CHAT", "書面", "1"] as [String?] {
            let shell = makeShell(mode: MemoryProfileModeStore(value))
            XCTAssertEqual(shell.profileMode, .auto, "\(value ?? "nil")")
            XCTAssertEqual(row284(Controller(shell, bundle: Self.textEdit)), Row284.formal, "\(value ?? "nil")")
        }
    }

    func testParseFiltersEntryByEntry() {
        typealias P = AppProfiles
        XCTAssertEqual(P.parse(nil), [:])
        XCTAssertEqual(P.parse("chat"), [:], "root is a string")
        XCTAssertEqual(P.parse([Self.textEdit, "chat"]), [:], "root is an array")
        XCTAssertEqual(P.parse(NSNumber(value: 1)), [:])
        XCTAssertEqual(P.parse([Self.textEdit: NSNumber(value: 0)] as [String: Any]), [:], "NSNumber value")
        XCTAssertEqual(P.parse([Self.textEdit: ["chat": "chat"]] as [String: Any]), [:], "nested dictionary")
        XCTAssertEqual(P.parse([Self.textEdit: "Chat"] as [String: Any]), [:], "other string")
        XCTAssertEqual(P.parse(["": "chat"] as [String: Any]), [:], "empty key")
        let mixed: [String: Any] = [
            Self.textEdit: "chat", Self.discord: "formal", Self.notes: "chat",
            "com.example.number": NSNumber(value: 1), "com.example.nested": ["a": "b"], "com.example.word": "casual", "": "formal",
        ]
        XCTAssertEqual(P.parse(mixed), [Self.textEdit: Shell.chat, Self.discord: Shell.formal, Self.notes: Shell.chat],
                       "one bad entry must not drop the valid ones")
        XCTAssertEqual(P.parse(NSDictionary(dictionary: mixed)), P.parse(mixed), "as UserDefaults returns it")
    }

    // MARK: read back at start (plan-verifier round 1)

    func testStoredProfileModeAppliesFromTheStart() {
        let chat = makeShell(mode: MemoryProfileModeStore("chat"))
        XCTAssertEqual(row284(Controller(chat, bundle: Self.textEdit)), Row284.chat)
        let formal = makeShell(mode: MemoryProfileModeStore("formal"))
        XCTAssertEqual(row284(Controller(formal, bundle: Self.discord)), Row284.formal)
    }

    func testStoredAppProfilesApplyFromTheStartAndSurviveTheNextWrite() {
        let apps = MemoryAppProfileStore([Self.textEdit: "chat", "com.example.bad": NSNumber(value: 1)] as [String: Any])
        let shell = makeShell(apps: apps)
        let textEdit = Controller(shell, bundle: Self.textEdit)
        XCTAssertEqual(row284(textEdit), Row284.chat)
        XCTAssertEqual(item(textEdit)?.checked, true)
        XCTAssertEqual(SettingsModel(shell: shell).appProfiles, [AppProfileRow(bundle: Self.textEdit, chat: true)])
        Controller(shell, bundle: Self.notes).session.perform(.toggleAppChat)
        XCTAssertEqual(stored(apps), [Self.textEdit: "chat", Self.notes: "chat"], "the saved entry was overwritten")
    }

    // MARK: composition owner (security review P2-1)

    /// A is composing when the menu is used in B: A's composition goes back to A (s3b section 5), never into B.
    func testSwitchInAnotherSessionCommitsTheOwnersCompositionToItsOwnClient() {
        let shell = makeShell()
        let a = Controller(shell, bundle: Self.textEdit), b = Controller(shell, bundle: Self.notes)
        a.session.activate()
        a.type("su3")
        XCTAssertEqual(a.client.marked, "你")
        b.session.perform(.toggleAppChat)
        XCTAssertEqual(a.client.text, "你")
        XCTAssertEqual(a.client.marked, "")
        XCTAssertEqual(b.client.calls, [], "B received A's composition")
        XCTAssertTrue(shell.owner === b.session)
        XCTAssertEqual(row284(b), Row284.chat)
    }

    /// Section 2.2 (review round): the window's changes reach the owner's composition at once, with no new activateServer, so
    /// Enter commits what the menu's checkmark now says.
    func testWindowChangesReapplyToTheComposingOwner() {
        let apps = MemoryAppProfileStore([Self.textEdit: "chat"])
        let shell = makeShell(apps: apps)
        let model = SettingsModel(shell: shell)
        let c = Controller(shell, bundle: Self.textEdit)
        c.client.selectedOverride = NSRange(location: NSNotFound, length: 0)
        c.session.activate()
        model.setProfileMode(.formal)                       // owner not composing: nothing reaches the client
        XCTAssertEqual(c.client.calls, [])
        c.type(Row284.standardKeys)
        XCTAssertEqual(c.client.marked, Row284.formal)
        model.setProfileMode(.chat)
        XCTAssertEqual(c.client.marked, Row284.chat, "the mode did not reach the composition")
        c.press(Keys.enter)
        XCTAssertEqual(c.client.text, Row284.chat)

        model.setProfileMode(.auto)                         // TextEdit set to chat: still chat
        c.type(Row284.standardKeys)
        XCTAssertEqual(c.client.marked, Row284.chat)
        model.removeAppProfile(Self.textEdit)
        XCTAssertEqual(c.client.marked, Row284.formal, "the removal did not reach the composition")
        c.press(Keys.enter)
        XCTAssertEqual(c.client.text, Row284.chat + Row284.formal)
        XCTAssertNil(apps.appProfiles)
    }

    // MARK: SettingsModel (section 2.4)

    func testPickerWritesTheStoreAndPosts() {
        let mode = MemoryProfileModeStore()
        let shell = makeShell(mode: mode)
        let model = SettingsModel(shell: shell)
        let c = Controller(shell, bundle: Self.textEdit)
        XCTAssertEqual(model.profileMode, .auto)
        let posted = XCTNSNotificationExpectation(name: Shell.didChangeSettings, object: shell)
        model.setProfileMode(.formal)
        wait(for: [posted], timeout: 0)
        XCTAssertEqual(mode.profileMode, "formal")
        XCTAssertEqual(model.profileMode, .formal)
        XCTAssertEqual(item(c)?.enabled, false)
        model.setProfileMode(.auto)
        XCTAssertEqual(mode.profileMode, "auto")
        XCTAssertEqual(item(c)?.enabled, true)
    }

    func testListFollowsTheMenuAndRemoveDropsOne() {
        let apps = MemoryAppProfileStore()
        let shell = makeShell(apps: apps)
        let model = SettingsModel(shell: shell)
        XCTAssertEqual(model.appProfiles, [])
        for bundle in [Self.notes, Self.textEdit, Self.discord] {
            Controller(shell, bundle: bundle).session.perform(.toggleAppChat)
        }
        let all = [AppProfileRow(bundle: Self.notes, chat: true), AppProfileRow(bundle: Self.textEdit, chat: true),
                   AppProfileRow(bundle: Self.discord, chat: false)]
        XCTAssertEqual(model.appProfiles, all.sorted { $0.bundle < $1.bundle }, "the model did not follow the menu")
        XCTAssertEqual(Set(model.appProfiles.map(\.bundle)), Set(stored(apps)?.keys.map { $0 } ?? []))
        model.removeAppProfile(Self.textEdit)
        XCTAssertEqual(model.appProfiles.map(\.bundle), [Self.notes, Self.discord])
        XCTAssertEqual(stored(apps), [Self.discord: "formal", Self.notes: "chat"])
        XCTAssertEqual(row284(Controller(shell, bundle: Self.textEdit)), Row284.formal, "removed: back to the built-in rule")
    }
}
