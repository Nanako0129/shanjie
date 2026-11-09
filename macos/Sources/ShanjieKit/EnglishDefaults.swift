import Foundation

/// docs/spec/english-mixed.md §10.2: the one-time reset of 重打同一個位置算英文 and 單按 Shift 切換中英 to off,
/// and the decision whether the person updated from an earlier version (and so is owed the explanation window). Pure: main.swift reads
/// the persistent domain and the learning folder, calls this, and writes the result back before the Shell, `Updates` and the updater
/// exist (they write `update*` keys, which would look like an earlier version).
public enum EnglishDefaultsReset {
    /// Written `true` once the reset ran; with it present nothing is done.
    public static let markerKey = "englishDefaults20261104"
    /// Written `true` for a person who updated; the explanation window clears it. A fresh install never gets it.
    public static let pendingKey = "englishDefaultsNoticePending"
    public static let overwriteKey = "englishOverwrite"
    public static let shiftToggleKey = "shiftToggle"

    /// The Bool values to write, by key. Empty when the marker is already there.
    /// - `domainKeys`: the keys of this app's own persistent domain (not `dictionaryRepresentation`, which holds system defaults).
    /// - `learningHasFiles`: the learning folder (`Application Support/shanjie/`) holds any file.
    /// - `markerPresent`: `markerKey` already exists in the domain.
    /// Any key other than the notice's own two (settings, `update*`, Sparkle's `SU*`) or any learning file means an earlier version.
    public static func writes(domainKeys: Set<String>, learningHasFiles: Bool, markerPresent: Bool) -> [String: Bool] {
        guard !markerPresent else { return [:] }
        var out = [overwriteKey: false, shiftToggleKey: false, markerKey: true]
        if learningHasFiles || !domainKeys.subtracting([markerKey, pendingKey]).isEmpty { out[pendingKey] = true }
        return out
    }
}

/// The words of the explanation window (docs/spec/english-mixed.md §10.3; the wording may change
/// without a new review of docs/spec/english-mixed.md §10.3).
public enum EnglishNoticeText {
    public static let title = "英打相關設定改成預設關"
    public static let intro = "這一版把下面兩個設定都改成關，你之前的設定也一起重設了。"
    public static let overwrite = "重打同一個位置算英文：開著時，在已經有注音的位置再按一個鍵，善解會把這一串當成英文。很多人打錯注音時，習慣不刪掉、直接接著打下一個注音，這個習慣剛好會被當成英文，看起來就像中文打到一半突然變成英文。常打 ok、rust、gta6 這類字的人，可以再打開。"
    public static let shift = "單按 Shift 切換中英：開著時，單按一下 Shift 就會切成英文；打字時不小心碰到 Shift，就會在沒注意時切走。"
    public static let how = "要再打開，到「設定 → 選字」。"
    public static let openSettings = "打開設定"
    public static let acknowledge = "知道了"
}
