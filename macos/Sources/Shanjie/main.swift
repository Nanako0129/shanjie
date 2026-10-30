import AppKit
import Carbon
import InputMethodKit
import ShanjieInstall
import ShanjieKit

// docs/contracts/s3b.md sections 4, 5 and 9. Arguments: none (run as the input method), exactly
// `install`, or exactly `--selftest`. Anything else exits non-zero before touching anything.
// Output is fixed text only (R2): no paths, bundle IDs or input.

func say(_ message: String) {
    FileHandle.standardError.write(Data("\(message)\n".utf8))
}

/// docs/contracts/s3b.md section 13.3, through ShanjieInstall (shared with the installer). Run on
/// the installed copy, by scripts/install-ime.sh or by the user after `brew install`. Exit 3 means
/// the mode is not listed yet or the input method is not accepted yet (add it in System Settings >
/// Keyboard > Input Sources); 1 is any other failure. installer-v2.md section 9: registration runs
/// only when the system does not know the mode yet or an earlier version's mode is enabled. When
/// it is skipped as not accepted, this waits up to 5 s for the system to take the input method
/// (calling nothing that changes TIS state) before giving up with 3.
func install() -> Int32 {
    guard let bundleID = Bundle.main.bundleIdentifier else {
        say("install: no bundle identifier")
        return 1
    }
    let result = Registration.run(bundleURL: Bundle.main.bundleURL, bundleID: bundleID, defaults: .standard)
    let accepted = Registration.waitUntilAccepted(result, isAccepted: { Registration.isAccepted(bundleID: bundleID) })
    let report = InstallCommand.report(result, acceptedAfterWait: accepted)
    for line in report.messages { say(line) }
    return report.exitCode
}

/// The chosen keyboard layout, in the app's own UserDefaults domain (its bundle ID), key `layout`
/// (section 13.2). Only the running input method creates this; tests use MemoryLayoutStore.
@MainActor
final class DefaultsLayoutStore: LayoutStore {
    var layout: String? {
        get { UserDefaults.standard.string(forKey: "layout") }
        set { UserDefaults.standard.set(newValue, forKey: "layout") }
    }
}

/// The "avoid ranking sensitive words first" switch (docs/contracts/sw-sensitive-demote.md section 3),
/// key `demoteSensitive` in the same domain; absent means on. Only the running input method creates this.
@MainActor
final class DefaultsDemoteStore: DemoteStore {
    var demote: Bool? {
        get { UserDefaults.standard.object(forKey: "demoteSensitive") as? Bool }
        set { UserDefaults.standard.set(newValue, forKey: "demoteSensitive") }
    }
}

/// The "即時預測" switch (docs/contracts/v3-engine.md section 10.5), key `prediction` in the same domain; absent means on.
@MainActor
final class DefaultsPredictionStore: PredictionStore {
    var prediction: Bool? {
        get { UserDefaults.standard.object(forKey: "prediction") as? Bool }
        set { UserDefaults.standard.set(newValue, forKey: "prediction") }
    }
    /// browser-first-syllable section 3, key `browserFirstSyllable`; absent means on.
    var browserFirstSyllable: Bool? {
        get { UserDefaults.standard.object(forKey: "browserFirstSyllable") as? Bool }
        set { UserDefaults.standard.set(newValue, forKey: "browserFirstSyllable") }
    }
}

/// The "可省略韻母" switch (docs/contracts/v3-engine.md section 12.1), key `abbreviation` in the same domain; absent means off.
@MainActor
final class DefaultsAbbreviationStore: AbbreviationStore {
    var abbreviation: Bool? {
        get { UserDefaults.standard.object(forKey: "abbreviation") as? Bool }
        set { UserDefaults.standard.set(newValue, forKey: "abbreviation") }
    }
}

/// The "中英文之間自動加空格" switch (docs/contracts/cjk-spacing.md section 2), key `cjkSpacing`; absent means off.
@MainActor
final class DefaultsSpacingStore: SpacingStore {
    var spacing: Bool? {
        get { UserDefaults.standard.object(forKey: "cjkSpacing") as? Bool }
        set { UserDefaults.standard.set(newValue, forKey: "cjkSpacing") }
    }
}

/// The "中英混打（實驗）" switch (docs/contracts/zhen-mixed.md section 3.2), key `english` in the same domain; absent means on (digits.md A.3.7), a saved value wins.
@MainActor
final class DefaultsEnglishStore: EnglishStore {
    var english: Bool? {
        get { UserDefaults.standard.object(forKey: "english") as? Bool }
        set { UserDefaults.standard.set(newValue, forKey: "english") }
    }
    /// The 重打同一個位置算英文 choice (docs/contracts/zhen-revert.md section 3), key `englishOverwrite`; absent means true.
    var englishOverwrite: Bool? {
        get { UserDefaults.standard.object(forKey: "englishOverwrite") as? Bool }
        set { UserDefaults.standard.set(newValue, forKey: "englishOverwrite") }
    }
    /// The 依按鍵順序判斷英文 choice (docs/contracts/zhen-keyorder.md section 3), key `englishKeyOrder`; absent means true (zhen-revert).
    var englishKeyOrder: Bool? {
        get { UserDefaults.standard.object(forKey: "englishKeyOrder") as? Bool }
        set { UserDefaults.standard.set(newValue, forKey: "englishKeyOrder") }
    }
}

/// The 模糊輸入 flags (docs/contracts/fuzzy-input.md section 2.2), key `fuzzyFlags` (an integer) in the same domain; absent means 0.
@MainActor
final class DefaultsFuzzyStore: FuzzyStore {
    var fuzzyFlags: UInt32? {
        get { (UserDefaults.standard.object(forKey: "fuzzyFlags") as? Int).flatMap { UInt32(exactly: $0) } }
        set { UserDefaults.standard.set(newValue.map { Int($0) }, forKey: "fuzzyFlags") }
    }
}

/// The "動漫與遊戲詞" switch (docs/contracts/acg-pack.md A.2), key `acgPack` in the same domain; absent means on (user decision 2026-10-09).
@MainActor
final class DefaultsAcgPackStore: AcgPackStore {
    var acgPack: Bool? {
        get { UserDefaults.standard.object(forKey: "acgPack") as? Bool }
        set { UserDefaults.standard.set(newValue, forKey: "acgPack") }
    }
}

/// The "經典台詞" switch (docs/contracts/quote-pack.md section 2), key `quotePack` in the same domain; absent means **off**.
@MainActor
final class DefaultsQuotePackStore: QuotePackStore {
    var quotePack: Bool? {
        get { UserDefaults.standard.object(forKey: "quotePack") as? Bool }
        set { UserDefaults.standard.set(newValue, forKey: "quotePack") }
    }
}

/// The "資訊與 AI 詞" switch (docs/contracts/it-pack.md section 3.2), key `itPack` in the same domain; absent means on.
@MainActor
final class DefaultsItPackStore: ItPackStore {
    var itPack: Bool? {
        get { UserDefaults.standard.object(forKey: "itPack") as? Bool }
        set { UserDefaults.standard.set(newValue, forKey: "itPack") }
    }
}

/// The 單按 Shift 切換中英 switch (docs/contracts/shift-toggle.md section 1), key `shiftToggle` in the same domain; absent means on.
@MainActor
final class DefaultsShiftToggleStore: ShiftToggleStore {
    var shiftToggle: Bool? {
        get { UserDefaults.standard.object(forKey: "shiftToggle") as? Bool }
        set { UserDefaults.standard.set(newValue, forKey: "shiftToggle") }
    }
}

/// The candidate glass tint slider (docs/contracts/settings-window.md section 2.3), key `glassTint` (Double); absent means 0.
@MainActor
final class DefaultsGlassTintStore: GlassTintStore {
    var glassTint: Double? {
        get { UserDefaults.standard.object(forKey: "glassTint") as? Double }
        set { UserDefaults.standard.set(newValue, forKey: "glassTint") }
    }
}

/// The candidate window's orientation (docs/contracts/candidate-vertical.md section 2.1), key `candidateVertical` (Bool); absent means
/// horizontal.
@MainActor
final class DefaultsCandidateOrientationStore: CandidateOrientationStore {
    var candidateVertical: Bool? {
        get { UserDefaults.standard.object(forKey: "candidateVertical") as? Bool }
        set { UserDefaults.standard.set(newValue, forKey: "candidateVertical") }
    }
}

/// The "語意校正" switch (docs/contracts/s7c2b-shell-rerank.md section 2.2), key `rerank` in the same domain; absent means on
/// (docs/contracts/s7h-tinylm-v3.md section 2.3).
@MainActor
final class DefaultsRerankStore: RerankStore {
    var rerank: Bool? {
        get { UserDefaults.standard.object(forKey: "rerank") as? Bool }
        set { UserDefaults.standard.set(newValue, forKey: "rerank") }
    }
    /// docs/contracts/rerank-strength.md section 3: key `rerankLevel` (`conservative`, `standard` or `aggressive`); absent or any
    /// other value is standard.
    var rerankLevel: String? {
        get { UserDefaults.standard.string(forKey: "rerankLevel") }
        set { UserDefaults.standard.set(newValue, forKey: "rerankLevel") }
    }
}

/// How chat or formal is chosen (docs/contracts/profile-choice.md section 2.1), key `profileMode` (`auto`, `chat` or `formal`);
/// absent or anything else means `auto`.
@MainActor
final class DefaultsProfileModeStore: ProfileModeStore {
    var profileMode: String? {
        get { UserDefaults.standard.string(forKey: "profileMode") }
        set { UserDefaults.standard.set(newValue, forKey: "profileMode") }
    }
}

/// The per-app choices (profile-choice section 2.1), key `appProfiles`: bundle ID to `chat` or `formal`, only for apps switched
/// from the menu. Read back raw (`AppProfiles.parse` filters it entry by entry); an empty dictionary removes the key. One
/// top-level key, not a key path: a bundle ID has dots.
@MainActor
final class DefaultsAppProfileStore: AppProfileStore {
    var appProfiles: Any? { UserDefaults.standard.object(forKey: "appProfiles") }
    func setAppProfiles(_ profiles: [String: String]) {
        if profiles.isEmpty {
            UserDefaults.standard.removeObject(forKey: "appProfiles")
        } else {
            UserDefaults.standard.set(profiles, forKey: "appProfiles")
        }
    }
}

/// The update settings and the last check (docs/contracts/about-update.md section 2): keys `updateAutoCheck` (Bool, absent
/// means on), `updateLastCheck` (Date) and `updateStatus` (a dictionary of strings, see `UpdateStore`) in the same domain.
@MainActor
final class DefaultsUpdateStore: UpdateStore {
    var autoCheck: Bool? {
        get { UserDefaults.standard.object(forKey: "updateAutoCheck") as? Bool }
        set { UserDefaults.standard.set(newValue, forKey: "updateAutoCheck") }
    }
    var lastCheck: Date? {
        get { UserDefaults.standard.object(forKey: "updateLastCheck") as? Date }
        set { UserDefaults.standard.set(newValue, forKey: "updateLastCheck") }
    }
    var status: [String: String]? {
        get { UserDefaults.standard.dictionary(forKey: "updateStatus") as? [String: String] }
        set { UserDefaults.standard.set(newValue, forKey: "updateStatus") }
    }
}

@MainActor
func runServer() -> Never {
    guard let bundleID = Bundle.main.bundleIdentifier,
          let connection = Bundle.main.infoDictionary?["InputMethodConnectionName"] as? String,
          let resources = Bundle.main.resourceURL
    else {
        say("shanjie: not running from the app bundle")
        exit(1)
    }
    let app = NSApplication.shared
    guard let server = IMKServer(name: connection, bundleIdentifier: bundleID) else {
        say("shanjie: cannot create the input method server")
        exit(1)
    }
    // Section 5: the engine is built after the IMK server exists.
    App.shell = Shell(
        resources: resources.absoluteURL, panel: CandidatePanelAdapter(),
        isSecureInput: { IsSecureEventInputEnabled() }, layoutStore: DefaultsLayoutStore(),
        learningDirectory: Shell.learningURL(), dialogs: AlertDialogs(), demoteStore: DefaultsDemoteStore(), predictionStore: DefaultsPredictionStore(), abbreviationStore: DefaultsAbbreviationStore(), englishStore: DefaultsEnglishStore(), acgPackStore: DefaultsAcgPackStore(), quotePackStore: DefaultsQuotePackStore(),
        glassTintStore: DefaultsGlassTintStore(), candidateOrientationStore: DefaultsCandidateOrientationStore(),
        rerankStore: DefaultsRerankStore(), rerankRunner: QueueRerankRunner(),
        profileModeStore: DefaultsProfileModeStore(), appProfileStore: DefaultsAppProfileStore(), fuzzyStore: DefaultsFuzzyStore(),
        // 可省略韻母 does not ship in v0.4.0 (user, 2026-10-10); see Shell.showsAbbreviation. Flip to true to offer it again.
        // This argument alone hides it (Shell's default true is for the tests) and the app target has no tests: the release
        // device check confirms the menu has no 可省略韻母.
        showsAbbreviation: false, spacingStore: DefaultsSpacingStore(), itPackStore: DefaultsItPackStore(),
        shiftToggleStore: DefaultsShiftToggleStore(), shiftHint: ShiftHintAdapter())
    // about-update section 2: the state always exists (the 關於 page shows the version); Sparkle starts only inside the .app.
    let version = Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0.0.0"
    let updates = Updates(store: DefaultsUpdateStore(), version: version)
    App.shell?.updates = updates
    let updater = Updater(updates: updates)
    let settings = SettingsWindowController(shell: App.shell)
    App.shell?.onOpenSettings = { settings.show() }
    updates.closeSettings = { settings.close() }
    withExtendedLifetime((server, updater)) { app.run() }
    exit(0)
}

let arguments = CommandLine.arguments
if arguments.count == 1 {
    MainActor.assumeIsolated { runServer() }
} else if arguments.count == 2 && arguments[1] == "install" {
    exit(install())
} else if arguments.count == 2 && arguments[1] == "--selftest" {
    guard let resources = Bundle.main.resourceURL else {
        say("selftest: no Resources directory")
        exit(1)
    }
    exit(MainActor.assumeIsolated { Selftest.run(resources: resources.absoluteURL) })
} else {
    say("usage: shanjie [install | --selftest]")
    exit(64)
}
