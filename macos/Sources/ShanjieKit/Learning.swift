import AppKit

// S4 in the shell (docs/contracts/s4-learning.md sections 2-4): the left context, privacyGate, the
// learning directory and the menu. R2 holds as in docs/contracts/s3b.md section 9: the context,
// bundle IDs and paths are never logged, stored or shown; logs are static text and codes only.

/// Why learning is paused for a client (section 3).
public enum LearningPause: Equatable, Sendable {
    case secureInput
    /// A denylisted app, or one whose bundle ID cannot be read.
    case app
}

/// Section 2: the left context the shell may pass to the core.
enum LeftContext {
    /// UTF-16 units requested before the insertion point.
    static let maxUTF16 = 32

    /// The range to request: at most `maxUTF16` units ending at `insertion`.
    static func range(insertion: Int) -> NSRange {
        let start = max(0, insertion - maxUTF16)
        return NSRange(location: start, length: insertion - start)
    }

    /// The trailing ≤ 2 consecutive Han characters of `text`, `nil` when there are none; the same
    /// rule and the same ranges as `learn::context_key` in core/src/learn.rs. `truncated` drops the
    /// first grapheme, which may be half of a surrogate pair or of a cluster.
    static func han(_ text: String, truncated: Bool) -> String? {
        let scalars = (truncated ? Substring(text.dropFirst()) : Substring(text)).unicodeScalars
        let tail = scalars.reversed().prefix { isHan($0) }.prefix(2)
        return tail.isEmpty ? nil : String(String.UnicodeScalarView(tail.reversed()))
    }

    /// Mirrors `is_han` in core/src/learn.rs.
    static func isHan(_ s: Unicode.Scalar) -> Bool {
        switch s.value {
        case 0x3400...0x4DBF, 0x4E00...0x9FFF, 0xF900...0xFAFF, 0x20000...0x2A6DF, 0x2A700...0x2EBEF,
             0x30000...0x3134F, 0x2F800...0x2FA1F, 0x3007: true
        default: false
        }
    }
}

/// One entry of the input method menu, built here so the tests can read it; the app turns it into
/// NSMenuItems. An entry without an action is shown disabled.
public struct MenuEntry: Equatable, Sendable {
    public enum Action: Equatable, Sendable {
        case layout(InputMode)
        case clear
        case toggleBackup
        case toggleDemote
        case togglePrediction
        case toggleAcgPack
        case openSettings
    }

    public var title: String
    public var action: Action?
    public var checked = false

    /// Fixed strings (R2: no app name, path or count).
    public enum Text {
        public static let pausedSecure = "學習已暫停（安全輸入）"
        public static let pausedApp = "學習已暫停（此 App）"
        public static let demote = "避免把敏感字詞排在前面"
        public static let prediction = "即時預測"
        public static let acgPack = "動漫與遊戲詞"
        public static let settings = "善解設定…"
        public static let clear = "清除選字記憶…"
        public static let excludeBackup = "不要備份選字記憶"
        public static let unavailable = "選字記憶無法存檔"
    }
}

/// The windows of the clear (section 4, user request 2026-10-05: "這個應該做成提示窗通知", replacing
/// the two-step confirmation inside the menu). The app shows them with NSAlert; tests answer for
/// the user.
@MainActor
public protocol LearningDialogs: AnyObject {
    /// "清除選字記憶…": calls `answer` with true only when the user chose 清除. It returns at once:
    /// the window must not block the input method while it is open; `answer` may never be called
    /// (a window already open ignores a second request).
    func confirmClear(_ answer: @escaping @MainActor (Bool) -> Void)
    /// The clear returned non-zero; never passed off as done.
    func clearFailed()
}

/// Fixed strings of those windows (R2: no app name, path or count).
public enum DialogText {
    public static let clearTitle = "要清除選字記憶嗎？"
    public static let clearMessage = "會刪除這台電腦上記住的改選與預測列選取紀錄，之後要重新學。清除只刪本機檔案；已經進 Time Machine 備份或本機快照的副本不受影響。"
    public static let clearButton = "清除"
    public static let cancel = "取消"
    public static let failedTitle = "清除選字記憶失敗"
    public static let failedMessage = "有檔案沒有刪掉，請再試一次。"
    public static let ok = "好"
}

extension Shell {
    /// Learning denylist (section 3): password managers. Looked up only, like `chatApps`; never
    /// passed to the core, logged or stored. Not checked on this machine (section 7.7).
    static let learningDenylist: Set<String> = [
        "com.1password.1password", "com.agilebits.onepassword7", "com.bitwarden.desktop",
        "org.keepassxc.keepassxc", "com.apple.Passwords", "com.apple.keychainaccess",
    ]

    /// `~/Library/Application Support/shanjie` for the app; `nil` if the system has no such folder.
    /// Nothing is created here: the core creates the directory (0700) in `learning_open`.
    public static func learningURL() -> URL? {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?
            .appendingPathComponent("shanjie", isDirectory: true)
    }

    /// privacyGate (section 3): paused on secure input, a denylisted app, or an unknown one.
    func learningPause(bundle: String?) -> LearningPause? {
        if isSecureInput() { return .secureInput }
        guard let bundle, !bundle.isEmpty, !Shell.learningDenylist.contains(bundle) else { return .app }
        return nil
    }

    /// Section 4: the core reports a failed write in bit0, or the file could not be opened.
    var learningUnavailable: Bool {
        learningOpenFailed || (engine?.learningStatus().map { $0 & 1 != 0 } ?? false)
    }

    /// Section 4: whether Time Machine skips the learning directory (default: backed up).
    var backupExcluded: Bool {
        (try? learningDirectory?.resourceValues(forKeys: [.isExcludedFromBackupKey]).isExcludedFromBackup) == true
    }

    /// Sets the flag on the directory (not the file, which a clear deletes). The directory exists
    /// once the core has opened it; a failure leaves the flag, and so the menu's checkmark, as it was.
    func setBackupExcluded(_ excluded: Bool) {
        guard var dir = learningDirectory else { return }
        var values = URLResourceValues()
        values.isExcludedFromBackup = excluded
        do { try dir.setResourceValues(values) } catch {
            Log.shell.error("learning directory: setting the backup exclusion failed")
        }
        NotificationCenter.default.post(name: Shell.didChangeSettings, object: self)
    }

    /// Section 4: memory, pending learns and the three files; non-zero opens the failure window.
    func clearLearning() {
        let code = engine?.learningClear() ?? 4
        // The settings window's "cannot save" row may change with the clear.
        NotificationCenter.default.post(name: Shell.didChangeSettings, object: self)
        guard code != 0 else { return }
        Log.shell.error("shanjie_engine_learning_clear failed, code \(code)")
        dialogs.clearFailed()
    }
}

extension Session {
    /// The gate for this session's client right now.
    public var learningPause: LearningPause? { shell.learningPause(bundle: client.bundleIdentifier) }

    /// Section 3: sampled before every core call that may learn, and sent every time (a rebuilt
    /// engine starts disabled). Returns whether learning is paused.
    @discardableResult
    func applyLearningGate(_ engine: CoreEngine) -> Bool {
        let paused = learningPause != nil
        let code = engine.setLearning(!paused)
        if code != 0 { Log.shell.error("shanjie_engine_set_learning failed, code \(code)") }
        return paused
    }

    /// Section 2: read once at composition start. No context when there is no insertion point or
    /// the client still holds marked text; at most `LeftContext.maxUTF16` units are requested.
    /// "No marked text" is an empty marked range at any location: on 2026-10-06 VS Code, Chrome,
    /// Finder's search field and others reported {insertion, 0} rather than NSTextInputClient's
    /// {NSNotFound, 0}, and one reported {NSNotFound, NSNotFound} (numbers-only log on device).
    func leftContext() -> String? {
        let selected = client.selectedRange(), marked = client.markedRange()
        guard selected.location != NSNotFound, marked.location == NSNotFound || marked.length == 0 else { return nil }
        let range = LeftContext.range(insertion: selected.location)
        guard range.length > 0, let text = client.attributedSubstring(from: range)?.string else { return nil }
        return LeftContext.han(text, truncated: range.location > 0)
    }

    /// The menu (s3b section 13.2, sw section 3 and S4 sections 3-4), in order.
    public var menu: [MenuEntry] {
        typealias T = MenuEntry.Text
        var items: [MenuEntry] = []
        switch learningPause {
        case .secureInput?: items.append(MenuEntry(title: T.pausedSecure))
        case .app?: items.append(MenuEntry(title: T.pausedApp))
        case nil: break
        }
        items.append(MenuEntry(title: "標準鍵盤", action: .layout(.standard), checked: layout == .standard))
        items.append(MenuEntry(title: "倚天鍵盤", action: .layout(.eten), checked: layout == .eten))
        items.append(MenuEntry(title: T.demote, action: .toggleDemote, checked: shell.demoteOn))
        items.append(MenuEntry(title: T.prediction, action: .togglePrediction, checked: shell.predictionOn))
        items.append(MenuEntry(title: T.acgPack, action: .toggleAcgPack, checked: shell.acgPackOn))
        items.append(MenuEntry(title: T.settings, action: .openSettings))
        items.append(MenuEntry(title: T.clear, action: .clear))
        items.append(MenuEntry(title: T.excludeBackup, action: .toggleBackup, checked: shell.backupExcluded))
        if shell.learningUnavailable { items.append(MenuEntry(title: T.unavailable)) }
        return items
    }

    public func perform(_ action: MenuEntry.Action) {
        switch action {
        case .layout(let m):
            shell.selectLayout(m)
        case .clear:
            shell.dialogs.confirmClear { [weak shell] clear in
                if clear { shell?.clearLearning() }
            }
        case .toggleBackup:
            shell.setBackupExcluded(!shell.backupExcluded)
        case .toggleDemote:
            applyDemote(!shell.demoteOn)
        case .togglePrediction:
            applyPrediction(!shell.predictionOn)
        case .toggleAcgPack:
            shell.setAcgPack(!shell.acgPackOn)
        case .openSettings:
            shell.openSettings()
        }
    }
}
