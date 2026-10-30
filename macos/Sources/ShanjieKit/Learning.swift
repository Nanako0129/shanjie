import AppKit

// S4 in the shell (docs/contracts/s4-learning.md sections 2-4): the left context, privacyGate, the
// learning directory and the menu. R2 holds as in docs/contracts/s3b.md section 9: the context,
// bundle IDs and paths are never logged; the context and paths are never stored or shown, and a bundle ID
// is stored and shown only for an app the user switched to chat or formal from the menu
// (docs/contracts/profile-choice.md section 2.5). Logs are static text and codes only.

/// Why learning is paused for a client (section 3).
public enum LearningPause: Equatable, Sendable {
    case secureInput
    /// A denylisted app, or one whose bundle ID cannot be read.
    case app
}

/// Section 2: the left context the shell may pass to the core (docs/contracts/context-return.md section 1: the raw text of the
/// same message, at most `maxGraphemes`; terminal apps only the Han tail).
enum LeftContext {
    /// UTF-16 units requested before the insertion point, and the most of a reply that is looked at (context-return section 1). A CJK
    /// or ASCII character is one unit (𠮷 two), so 256 units hold more than 64 of them; emoji are longer and fewer fit: 256 units hold
    /// 64 of 👍🏽 (four units) and 32 of the ZWJ family 👨‍👩‍👧 (eight units), as LearningTests measures.
    static let maxUTF16 = 256
    /// Graphemes passed to the core.
    static let maxGraphemes = 64

    /// Terminal apps get only `han`, as before (context-return section 1; user decision 2026-10-25, security review P2-1): a terminal
    /// line holds commands and their output. com.apple.Terminal and com.googlecode.iterm2 were read from the apps' Info.plist
    /// (CFBundleIdentifier) on the maintainer's Mac on 2026-10-25; org.alacritty was corrected in review (PR #294); the other five are
    /// the IDs the contract lists, not checked against an installed app or a published Info.plist.
    /// An editor with a built-in terminal is not here: the panel has the editor's bundle ID (a known limit, out of scope).
    static let terminals: Set<String> = [
        "com.apple.Terminal", "com.googlecode.iterm2", "dev.warp.Warp-Stable", "net.kovidgoyal.kitty", "com.github.wez.wezterm",
        "org.alacritty", "co.zeit.hyper", "com.mitchellh.ghostty",
    ]

    /// The range to request: at most `maxUTF16` units ending at `insertion`.
    static func range(insertion: Int) -> NSRange {
        let start = max(0, insertion - maxUTF16)
        return NSRange(location: start, length: insertion - start)
    }

    /// Step 1 (P3-2): a reply longer than `maxUTF16` units (an app may return more than was asked) is cut to its last `maxUTF16`
    /// units and counts as truncated. A surrogate pair cut in half decodes to U+FFFD, which the truncated drop removes.
    static func clamp(_ text: String) -> (text: String, truncated: Bool) {
        let units = text.utf16
        guard units.count > maxUTF16 else { return (text, false) }
        return (String(decoding: units.suffix(maxUTF16), as: UTF16.self), true)
    }

    /// Steps 2-5: drop the first grapheme of a truncated read, start after the last line break (`Character.isNewline`) or NUL (a
    /// NUL is a break, not deleted: deleting it would join 中\0文 into 中文), keep the last `maxGraphemes` graphemes as they are
    /// (punctuation, spaces, English and emoji included). `nil` when nothing is left.
    static func raw(_ text: String, truncated: Bool) -> String? {
        let line = truncated ? Substring(text.dropFirst()) : Substring(text)
        let start = line.lastIndex { $0.isNewline || $0 == "\0" }.map(line.index(after:)) ?? line.startIndex
        let kept = line[start...].suffix(maxGraphemes)
        return kept.isEmpty ? nil : String(kept)
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
        case toggleEnglish
        case toggleAbbreviation
        case toggleRerank
        // No menu item carries these since it-pack contract 3.2 (the switches are in Settings); `perform` keeps them for programmatic use.
        case toggleAcgPack
        case toggleItPack
        case toggleAppChat
        case openSettings
        /// docs/contracts/about-update.md section 1: a silent check, then 善解更新 (standalone-updater.md) if it confirms one.
        case installUpdate
    }

    public var title: String
    public var action: Action?
    public var checked = false
    /// False: shown greyed (the checkmark still shows the setting). The app drops the action of a disabled entry.
    public var enabled = true

    /// Fixed strings (R2: no app name, path or count).
    public enum Text {
        public static let pausedSecure = "學習已暫停（安全輸入）"
        public static let pausedApp = "學習已暫停（此 App）"
        public static let demote = "避免把敏感字詞排在前面"
        public static let prediction = "即時預測"
        /// The browser toggle under 即時預測 in the settings window and the line under it (browser-first-syllable section 3).
        public static let browserFirstSyllable = "在瀏覽器裡，第一個字不顯示預測"
        public static let browserFirstSyllableNote = "在 Chrome 編輯 Google 文件時，第一個字回給輸入法的位置是錯的，預測列會跑到視窗左上角。Edge、Brave 等 Chromium 瀏覽器也一起套用。"
        public static let english = "中英混打（實驗）"
        /// The line under that toggle in the settings window (docs/contracts/digits.md A.3.7).
        public static let englishDigitsNote = "打英文不用切換輸入法：打的字不可能是注音時直接變成英文，留在組字區；按空白才發現音節不存在（例如 so）也一樣，那一種要用 Backspace 修，不能按 Esc 變回注音；數字列的鍵也會打出數字（例如 2026年、3.14）。"
        /// The reversal-guard toggle and the line under it (docs/contracts/zhen-reapply.md section 1.2).
        public static let englishReversalGuard = "快打時鍵序顛倒不當英文"
        public static let englishReversalGuardNote = "快打時，同一個字的注音鍵先後顛倒（例如 ㄍㄜˋ 打成 ㄜㄍˋ）照注音處理，不會變成英文。關掉而且「依按鍵順序判斷英文」開著時，ls、mv 這類字不用按 Shift，但快打容易誤觸。"
        /// The key-order toggle and the line under it (docs/contracts/zhen-keyorder.md section 3, zhen-revert section 3).
        public static let englishKeyOrder = "依按鍵順序判斷英文"
        public static let englishKeyOrderNote = "打了聲調鍵才發現音節不存在時，整段變成英文（馬上按 Esc 變回注音）；「快打時鍵序顛倒不當英文」關著時，注音鍵沒照順序也會變成英文。"
        /// The toggle after it and its line (zhen-revert section 3).
        public static let englishOverwrite = "重打同一個位置算英文"
        public static let englishOverwriteNote = "ok、rust、gta6 這類字要靠它。開了之後，用重打同一個位置來改錯的注音也會變成英文（馬上按 Esc 變回注音）。"
        /// The Shift-toggle switch in the settings window and the line under it (docs/contracts/shift-toggle.md section 1).
        public static let shiftToggle = "單按 Shift 切換中英"
        public static let shiftToggleNote = "單按一下左或右 Shift 在中文和英文之間切換，游標旁會短暫顯示「英」或「中」。每個 App 各自記住；組字區有字時，之後打的字母接在後面當英文。"
        /// The spacing toggle in the settings window and the line under it (docs/contracts/cjk-spacing.md section 2).
        public static let spacing = "中英文之間自動加空格"
        public static let spacingNote = "在漢字和半形英文、數字之間自動加一個空格（俗稱「盤古之白」）。只改善解送出的文字。"
        public static let abbreviation = "可省略韻母"
        public static let rerank = "語意校正"
        public static let acgPack = "動漫與遊戲詞"
        public static let quotePack = "經典台詞"
        public static let itPack = "資訊與 AI 詞"
        public static func acgDataDate(_ date: String) -> String { "資料更新至 \(date)（維基百科）" }
        /// The menu's non-clickable pack line (it-pack contract 3.2).
        public static func packsOn(_ names: [String]) -> String { "已開啟的詞包：" + names.joined(separator: "、") }
        /// No app name in the title (profile-choice section 2.3).
        public static let appChat = "這個 App 用聊天設定"
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
    /// Names the new words too (new-word B, docs/contracts/newword-b.md section 2.7).
    public static let clearMessage = "會刪除這台電腦上記住的改選、預測列選取，以及從逐字改選學到的新詞，之後要重新學。清除只刪本機檔案；已經進 Time Machine 備份或本機快照的副本不受影響。"
    public static let clearButton = "清除"
    public static let cancel = "取消"
    public static let failedTitle = "清除選字記憶失敗"
    public static let failedMessage = "有檔案沒有刪掉，請再試一次。"
    public static let ok = "好"
}

extension Shell {
    /// Learning denylist (section 3): password managers. Looked up only; never passed to the core,
    /// logged or stored. The user's per-app chat or formal choices (`appProfiles`) do not affect it
    /// (profile-choice section 2.5). Not checked on this machine (section 7.7).
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
        changed()
    }

    /// Section 4: memory, pending learns and the three files; non-zero opens the failure window.
    func clearLearning() {
        let code = engine?.learningClear() ?? 4
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
        applyLearningGate(engine, bundle: client.bundleIdentifier)
    }

    /// The gate for `bundle`, this client's bundle ID as the caller read it (a synchronous IPC, read once per key by `send`).
    @discardableResult
    func applyLearningGate(_ engine: CoreEngine, bundle: String?) -> Bool {
        let paused = shell.learningPause(bundle: bundle) != nil
        let code = engine.setLearning(!paused)
        if code != 0 { Log.shell.error("shanjie_engine_set_learning failed, code \(code)") }
        return paused
    }

    /// Section 2: read once at composition start. No context when there is no insertion point or
    /// the client still holds marked text; at most `LeftContext.maxUTF16` units are requested, and no more of the reply is read.
    /// "No marked text" is an empty marked range at any location: on 2026-10-06 VS Code, Chrome,
    /// Finder's search field and others reported {insertion, 0} rather than NSTextInputClient's
    /// {NSNotFound, 0}, and one reported {NSNotFound, NSNotFound} (numbers-only log on device).
    ///
    /// Returns what goes to the core (context-return section 1: `LeftContext.raw`, or `LeftContext.han` for a terminal app; `bundle` is
    /// this client's bundle ID as the gate read it, no second IPC) and, from the same read, the raw last character for cjk-spacing
    /// section 1. Only in memory: never stored or logged. Changes no state.
    func leftContext(bundle: String?) -> (text: String?, last: Character?) {
        let selected = client.selectedRange(), marked = client.markedRange()
        guard selected.location != NSNotFound, marked.location == NSNotFound || marked.length == 0 else { return (nil, nil) }
        let range = LeftContext.range(insertion: selected.location)
        guard range.length > 0, let reply = client.attributedSubstring(from: range)?.string else { return (nil, nil) }
        let (text, clamped) = LeftContext.clamp(reply)
        let truncated = clamped || range.location > 0
        let terminal = bundle.map(LeftContext.terminals.contains) ?? false
        return (terminal ? LeftContext.han(text, truncated: truncated) : LeftContext.raw(text, truncated: truncated), text.last)
    }

    /// The menu (s3b section 13.2, sw section 3 and S4 sections 3-4), in order.
    public var menu: [MenuEntry] {
        typealias T = MenuEntry.Text
        var items: [MenuEntry] = []
        let bundle = client.bundleIdentifier  // a synchronous IPC: read once per menu
        switch shell.learningPause(bundle: bundle) {
        case .secureInput?: items.append(MenuEntry(title: T.pausedSecure))
        case .app?: items.append(MenuEntry(title: T.pausedApp))
        case nil: break
        }
        items.append(MenuEntry(title: "標準鍵盤", action: .layout(.standard), checked: layout == .standard))
        items.append(MenuEntry(title: "倚天鍵盤", action: .layout(.eten), checked: layout == .eten))
        items.append(MenuEntry(title: T.demote, action: .toggleDemote, checked: shell.demoteOn))
        items.append(MenuEntry(title: T.prediction, action: .togglePrediction, checked: shell.predictionOn))
        // Greyed while the prediction row is off: the units it makes can only be resolved from the row (section 12.1).
        if shell.showsAbbreviation {
            items.append(MenuEntry(title: T.abbreviation, action: .toggleAbbreviation, checked: shell.abbreviationOn, enabled: shell.predictionOn))
        }
        // zhen-mixed section 3.2: after 即時預測 and before 語意校正, where 可省略韻母 sits when it is shown (the app hides it, so there it
        // follows 即時預測 directly); greyed (checkmark kept) when the layout is Eten.
        items.append(MenuEntry(title: T.english, action: .toggleEnglish, checked: shell.englishOn, enabled: shell.englishEnabled))
        // s7c2b section 2.3 (F11): greyed while the demotion table is off or the small model did not load, where the core makes
        // no rerank job.
        items.append(MenuEntry(title: T.rerank, action: .toggleRerank, checked: shell.rerankOn, enabled: shell.rerankEnabled))
        // it-pack contract 3.2: the pack switches live in Settings; the menu only names the packs the engine loaded, not clickable,
        // and has no line when none is loaded (the ACG data date is shown under its switch in Settings).
        let packs = shell.loadedPackTitles
        if !packs.isEmpty { items.append(MenuEntry(title: T.packsOn(packs))) }
        // profile-choice section 2.3: absent without a bundle ID (nothing to store); greyed outside auto, checked as forced.
        if let bundle, !bundle.isEmpty {
            items.append(MenuEntry(title: T.appChat, action: .toggleAppChat, checked: profile(for: bundle) == Shell.chat,
                                   enabled: shell.profileMode == .auto))
        }
        // about-update section 1: only while a newer version is known; never shown otherwise.
        // Greyed while a check runs (the press would be ignored).
        if let u = shell.updates, let v = u.available {
            items.append(MenuEntry(title: UpdateText.menu(v), action: .installUpdate, enabled: !u.checking))
        }
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
            shell.confirmAndClear()
        case .toggleBackup:
            shell.setBackupExcluded(!shell.backupExcluded)
        case .toggleDemote:
            applyDemote(!shell.demoteOn)
        case .togglePrediction:
            applyPrediction(!shell.predictionOn)
        case .toggleEnglish:
            applyEnglish(!shell.englishOn)
        case .toggleAbbreviation:
            applyAbbreviation(!shell.abbreviationOn)
        case .toggleRerank:
            // To the owner of the composition, not this session: the core re-decodes the owner's composition, so this session
            // could not show the snapshot.
            shell.applyRerank(!shell.rerankOn)
        case .toggleAcgPack:
            shell.setAcgPack(!shell.acgPackOn)
        case .toggleItPack:
            shell.setItPack(!shell.itPackOn)
        case .toggleAppChat:
            toggleAppChat()
        case .openSettings:
            shell.openSettings()
        case .installUpdate:
            shell.updates?.install()
        }
    }
}
