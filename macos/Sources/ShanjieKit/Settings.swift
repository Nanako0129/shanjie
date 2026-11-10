import Combine
import Foundation

/// One row of the 個別設定的 App list (docs/spec/macos-shell.md §9.3).
public struct AppProfileRow: Equatable, Hashable, Sendable {
    public let bundle: String
    /// True: 聊天; false: 書面.
    public let chat: Bool
}

/// The settings window's view model (docs/spec/macos-shell.md §9.3). Every control goes
/// through the same Shell setters and stores as the input method menu, and every value is re-read
/// when the Shell posts `didChangeSettings`, so the window and the menu never disagree. The SwiftUI
/// form in the app target only binds to this.
@MainActor
public final class SettingsModel: ObservableObject {
    private let shell: Shell
    private var observer: NSObjectProtocol?

    @Published public private(set) var layout = InputMode.standard
    @Published public private(set) var prediction = true
    /// 在瀏覽器裡，第一個字不顯示預測 (docs/spec/prediction.md §9.4), greyed while the prediction row is off.
    @Published public private(set) var browserFirstSyllable = false
    @Published public private(set) var abbreviation = false
    /// The 中英混打（實驗） toggle (docs/spec/english-mixed.md §1.1, §11), and whether it is enabled: the standard layout, like
    /// the menu item (false: the toggle is greyed, the setting kept).
    @Published public private(set) var english = false
    @Published public private(set) var englishEnabled = true
    /// The 模糊輸入 flags (docs/spec/decoder.md §6.1): bits 0-3 the four sound pairs, bit 4 the adjacent-key slips, as
    /// `shanjie_engine_set_fuzzy`. `fuzzyAdjacentEnabled` is false in the Eten layout, which greys the adjacent-key checkbox (the
    /// value is kept).
    @Published public private(set) var fuzzyFlags: UInt32 = 0
    @Published public private(set) var fuzzyAdjacentEnabled = true
    /// The 重打同一個位置算英文 choice under the 中英混打 toggle (docs/spec/english-mixed.md §11): a key over the symbol already in its column reverts
    /// to the raw keys as English. Default false since docs/spec/english-mixed.md §10.1. Enabled when the toggle is enabled and on.
    @Published public private(set) var englishOverwrite = false
    @Published public private(set) var englishOverwriteEnabled = false
    /// The 依按鍵順序判斷英文 choice (docs/spec/english-mixed.md §11, default true since zhen-revert): a key out of column order, and a tone key
    /// that makes a syllable the lexicon lacks, revert to English. Enabled on the overwrite choice's condition.
    @Published public private(set) var englishKeyOrder = true
    @Published public private(set) var englishKeyOrderEnabled = false
    /// The 快打時鍵序顛倒不當英文 choice (docs/spec/english-mixed.md §1.1, §3.2, default true): while on, a key out of column order is never English.
    @Published public private(set) var englishReversalGuard = true
    @Published public private(set) var englishReversalGuardEnabled = false
    /// 中英文之間自動加空格 (docs/spec/english-mixed.md §9, §11).
    @Published public private(set) var spacing = false
    /// The abbreviation toggle is shown only when the shell offers it (`Shell.showsAbbreviation`).
    public var showsAbbreviation: Bool { shell.showsAbbreviation }
    /// The 關於 page's update state (docs/spec/install-and-update.md §3.4–§3.6); it publishes its own changes.
    public var updates: Updates? { shell.updates }
    @Published public private(set) var demote = true
    /// 語意校正 (docs/spec/semantic-rerank.md §8.2).
    @Published public private(set) var rerank = true
    /// Whether the window's 語意校正 toggle is enabled: the demotion table on and the small model loaded, like the menu item.
    @Published public private(set) var rerankEnabled = true
    /// 語意校正的強度 (docs/spec/semantic-rerank.md §8.2), and whether its control is enabled (`Shell.rerankLevelEnabled`).
    @Published public private(set) var rerankLevel = RerankLevel.standard
    @Published public private(set) var rerankLevelEnabled = false
    @Published public private(set) var acgPack = true
    /// 經典台詞 (docs/spec/lexicon-and-packs.md §7.2, §4.1, §4.2): off until chosen.
    @Published public private(set) var quotePack = false
    @Published public private(set) var itPack = true
    /// 美妝與保養詞 (docs/spec/lexicon-and-packs.md §4.2, §8.3): on until chosen otherwise.
    @Published public private(set) var beautyPack = true
    /// 單按 Shift 切換中英 (docs/spec/keys-and-composition.md §9.1). Default false since docs/spec/english-mixed.md §10.1.
    @Published public private(set) var shiftToggle = false
    /// The grey "資料更新至 …" line under the 動漫與遊戲詞 toggle (docs/spec/lexicon-and-packs.md §4.5): the same text as the menu's, `nil` for no line.
    public var acgDataLine: String? { shell.acgDataDate.map(MenuEntry.Text.acgDataDate) }
    @Published public private(set) var backupExcluded = false
    @Published public private(set) var glassTint = 0.0
    /// The 候選窗方向 control (docs/spec/macos-shell.md §8.6.1): true is 直排.
    @Published public private(set) var candidateVertical = false
    /// The 切換方式 picker (docs/spec/macos-shell.md §9.3).
    @Published public private(set) var profileMode = ProfileMode.auto
    /// The 個別設定的 App list, ordered by bundle ID. Names and icons are looked up in the app target, which orders by name;
    /// this model gives only the bundle ID and the setting, so the tests never touch NSWorkspace.
    @Published public private(set) var appProfiles: [AppProfileRow] = []
    /// Status rows (docs/spec/macos-shell.md §9.3). There is no "this app" row: it comes from one session's client and
    /// this window belongs to no session.
    @Published public private(set) var pausedSecure = false
    @Published public private(set) var unavailable = false
    /// 診斷 (docs/spec/macos-shell.md §13.3): the switch, and whether there is anything to copy (the button is greyed when not). The log
    /// announces no change, so `diagLogEmpty` is read with the external state.
    @Published public private(set) var diagLog = true
    @Published public private(set) var diagLogEmpty = true
    /// The text the preview sheet shows (docs/spec/macos-shell.md §13.3), taken once when it opens; `nil` while it is closed.
    @Published public private(set) var diagPreview: String?

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
        browserFirstSyllable = shell.browserFirstSyllableOn
        abbreviation = shell.abbreviationOn
        spacing = shell.spacingOn
        english = shell.englishOn
        englishEnabled = shell.englishEnabled
        fuzzyFlags = shell.fuzzyFlags
        fuzzyAdjacentEnabled = shell.fuzzyAdjacentEnabled
        englishOverwrite = shell.englishOverwriteOn
        englishOverwriteEnabled = shell.englishOverwriteEnabled
        englishKeyOrder = shell.englishKeyOrderOn
        englishKeyOrderEnabled = shell.englishKeyOrderEnabled
        englishReversalGuard = shell.englishReversalGuardOn
        englishReversalGuardEnabled = shell.englishReversalGuardEnabled
        demote = shell.demoteOn
        rerank = shell.rerankOn
        rerankEnabled = shell.rerankEnabled
        rerankLevel = shell.rerankLevel
        rerankLevelEnabled = shell.rerankLevelEnabled
        acgPack = shell.acgPackOn
        quotePack = shell.quotePackOn
        itPack = shell.itPackOn
        beautyPack = shell.beautyPackOn
        shiftToggle = shell.shiftToggleOn
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
        diagLog = shell.diag.enabled
        diagLogEmpty = shell.diag.isEmpty
    }

    public func setDiagLog(_ on: Bool) {
        shell.diag.enabled = on
        refreshExternal()
    }

    /// 複製診斷紀錄…: the export, taken now, is what the sheet shows.
    public func openDiagPreview() { diagPreview = shell.diag.export(version: shell.updates?.version ?? "0.0.0") }

    /// 複製: the very text the sheet showed (not a new export), then the sheet closes.
    public func copyDiagPreview(to pasteboard: DiagPasteboard) {
        if let diagPreview { DiagLog.copy(diagPreview, to: pasteboard) }
        diagPreview = nil
    }

    public func closeDiagPreview() { diagPreview = nil }

    public func selectLayout(_ m: InputMode) { shell.selectLayout(m) }
    public func setPrediction(_ on: Bool) { shell.applyPrediction(on) }
    public func setAbbreviation(_ on: Bool) { shell.applyAbbreviation(on) }
    public func setSpacing(_ on: Bool) { shell.setSpacing(on) }
    public func setBrowserFirstSyllable(_ on: Bool) { shell.setBrowserFirstSyllable(on) }
    public func setEnglish(_ on: Bool) { shell.applyEnglish(on) }
    /// One checkbox of 模糊輸入: `bit` is a bit of the flags (0-3 the pairs, 4 the adjacent-key slips).
    public func setFuzzy(bit: Int, _ on: Bool) {
        precondition((0..<5).contains(bit))
        shell.applyFuzzy(on ? shell.fuzzyFlags | (1 << UInt32(bit)) : shell.fuzzyFlags & ~(1 << UInt32(bit)))
    }
    public func setEnglishOverwrite(_ on: Bool) { shell.applyEnglishOverwrite(on) }
    public func setEnglishKeyOrder(_ on: Bool) { shell.applyEnglishKeyOrder(on) }
    public func setEnglishReversalGuard(_ on: Bool) { shell.applyEnglishReversalGuard(on) }
    public func setDemote(_ on: Bool) { shell.applyDemote(on) }
    public func setRerank(_ on: Bool) { shell.applyRerank(on) }
    public func setRerankLevel(_ level: RerankLevel) { shell.applyRerankLevel(level) }
    public func setAcgPack(_ on: Bool) { shell.setAcgPack(on) }
    public func setQuotePack(_ on: Bool) { shell.setQuotePack(on) }
    public func setItPack(_ on: Bool) { shell.setItPack(on) }
    public func setBeautyPack(_ on: Bool) { shell.setBeautyPack(on) }
    public func setShiftToggle(_ on: Bool) { shell.applyShiftToggle(on) }
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

/// The settings window's height (docs/spec/macos-shell.md §9.2): one fixed comfortable size, clamped into the screen so a
/// small display never gets a window taller than its usable area.
public enum SettingsLayout {
    /// Frame height (content plus title bar) in points, a starting value for the device rounds (budget: 3 rounds), not
    /// measured against a reference. Measured on a Mac: the built-in display's usable height is 1074.5 pt
    /// (1710 x 1112) and the external one's 1409 pt, so 620 leaves room above and below on both.
    public static let preferredHeight: CGFloat = 620

    public static func frameHeight(visibleHeight: CGFloat) -> CGFloat {
        min(preferredHeight, visibleHeight)
    }
}

/// docs/spec/macos-shell.md §9.2: the settings window's frame moved vertically into its screen's usable area, up when it
/// hangs below (the Dock), down when it sticks out above (the menu bar). The window's height is capped to the area's, so
/// both never apply at once.
public func settingsFrame(_ frame: CGRect, clampedInto area: CGRect) -> CGRect {
    var r = frame
    if r.minY < area.minY { r.origin.y = area.minY }
    if r.maxY > area.maxY { r.origin.y = area.maxY - r.height }
    return r
}
