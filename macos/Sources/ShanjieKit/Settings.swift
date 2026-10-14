import Combine
import Foundation

/// One row of the 個別設定的 App list (profile-choice section 2.4).
public struct AppProfileRow: Equatable, Hashable, Sendable {
    public let bundle: String
    /// True: 聊天; false: 書面.
    public let chat: Bool
}

/// The settings window's view model (docs/contracts/settings-window.md section 2.2). Every control goes
/// through the same Shell setters and stores as the input method menu, and every value is re-read
/// when the Shell posts `didChangeSettings`, so the window and the menu never disagree. The SwiftUI
/// form in the app target only binds to this.
@MainActor
public final class SettingsModel: ObservableObject {
    private let shell: Shell
    private var observer: NSObjectProtocol?

    @Published public private(set) var layout = InputMode.standard
    @Published public private(set) var prediction = true
    @Published public private(set) var abbreviation = false
    /// The 中英混打（實驗） toggle (zhen-mixed section 3.2), and whether it is enabled: the word list loaded and the standard layout, like
    /// the menu item (false: the toggle is greyed, the setting kept).
    @Published public private(set) var english = false
    @Published public private(set) var englishEnabled = true
    /// The 英文的顯示 choice under it (zhen-inline section 2): true shows the English as it is typed. Enabled when the toggle is
    /// enabled and on.
    @Published public private(set) var englishInline = true
    @Published public private(set) var englishInlineEnabled = false
    /// The abbreviation toggle is shown only when the shell offers it (`Shell.showsAbbreviation`).
    public var showsAbbreviation: Bool { shell.showsAbbreviation }
    @Published public private(set) var demote = true
    /// 語意校正 (s7c2b section 2.2).
    @Published public private(set) var rerank = true
    /// Whether the window's 語意校正 toggle is enabled: the demotion table on and the small model loaded, like the menu item.
    @Published public private(set) var rerankEnabled = true
    /// 語意校正的強度 (docs/contracts/rerank-strength.md section 3), and whether its control is enabled (`Shell.rerankLevelEnabled`).
    @Published public private(set) var rerankLevel = RerankLevel.standard
    @Published public private(set) var rerankLevelEnabled = false
    @Published public private(set) var acgPack = true
    /// The grey "資料更新至 …" line under the 動漫與遊戲詞 toggle (acg-pack contract A2.4): the same text as the menu's, `nil` for no line.
    public var acgDataLine: String? { shell.acgDataDate.map(MenuEntry.Text.acgDataDate) }
    @Published public private(set) var backupExcluded = false
    @Published public private(set) var glassTint = 0.0
    /// The 候選窗方向 control (candidate-vertical contract section 2.1): true is 直排.
    @Published public private(set) var candidateVertical = false
    /// The 切換方式 picker (profile-choice section 2.4).
    @Published public private(set) var profileMode = ProfileMode.auto
    /// The 個別設定的 App list, ordered by bundle ID. Names and icons are looked up in the app target, which orders by name;
    /// this model gives only the bundle ID and the setting, so the tests never touch NSWorkspace.
    @Published public private(set) var appProfiles: [AppProfileRow] = []
    /// Status rows (section 2.2). There is no "this app" row: it comes from one session's client and
    /// this window belongs to no session.
    @Published public private(set) var pausedSecure = false
    @Published public private(set) var unavailable = false

    public init(shell: Shell) {
        self.shell = shell
        refresh()
        observer = NotificationCenter.default.addObserver(forName: Shell.didChangeSettings, object: shell, queue: .main) {
            [weak self] _ in MainActor.assumeIsolated { self?.refreshSettings() }
        }
    }

    isolated deinit {
        if let observer { NotificationCenter.default.removeObserver(observer) }
    }

    public func refresh() {
        refreshSettings()
        refreshExternal()
    }

    /// Shell's cached values only; this runs on every change notification, a slider drag included.
    func refreshSettings() {
        layout = shell.layout
        prediction = shell.predictionOn
        abbreviation = shell.abbreviationOn
        english = shell.englishOn
        englishEnabled = shell.englishEnabled
        englishInline = shell.englishInlineOn
        englishInlineEnabled = shell.englishInlineEnabled
        demote = shell.demoteOn
        rerank = shell.rerankOn
        rerankEnabled = shell.rerankEnabled
        rerankLevel = shell.rerankLevel
        rerankLevelEnabled = shell.rerankLevelEnabled
        acgPack = shell.acgPackOn
        glassTint = shell.glassTint
        candidateVertical = shell.candidateVertical
        profileMode = shell.profileMode
        appProfiles = shell.appProfiles.keys.sorted().map { AppProfileRow(bundle: $0, chat: shell.appProfiles[$0] == Shell.chat) }
    }

    /// State the Shell does not announce or caches nowhere: secure input, the backup flag (a
    /// resourceValues read) and the core's write status. Read when the window opens and becomes key.
    public func refreshExternal() {
        backupExcluded = shell.backupExcluded
        pausedSecure = shell.isSecureInput()
        unavailable = shell.learningUnavailable
    }

    public func selectLayout(_ m: InputMode) { shell.selectLayout(m) }
    public func setPrediction(_ on: Bool) { shell.applyPrediction(on) }
    public func setAbbreviation(_ on: Bool) { shell.applyAbbreviation(on) }
    public func setEnglish(_ on: Bool) { shell.applyEnglish(on) }
    public func setEnglishInline(_ on: Bool) { shell.applyEnglishInline(on) }
    public func setDemote(_ on: Bool) { shell.applyDemote(on) }
    public func setRerank(_ on: Bool) { shell.applyRerank(on) }
    public func setRerankLevel(_ level: RerankLevel) { shell.applyRerankLevel(level) }
    public func setAcgPack(_ on: Bool) { shell.setAcgPack(on) }
    public func setBackupExcluded(_ on: Bool) {
        shell.setBackupExcluded(on)
        refreshExternal()  // not read on the change notification
    }
    public func setGlassTint(_ v: Double) { shell.setGlassTint(v) }
    public func setCandidateVertical(_ on: Bool) { shell.applyCandidateVertical(on) }
    public func setProfileMode(_ m: ProfileMode) { shell.setProfileMode(m) }
    public func removeAppProfile(_ bundle: String) { shell.removeAppProfile(bundle) }
    /// The same confirmation window as the menu's.
    public func clear() {
        shell.confirmAndClear { [weak self] in self?.refreshExternal() }  // the "cannot save" row may change
    }
}
