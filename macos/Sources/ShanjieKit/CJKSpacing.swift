/// docs/spec/english-mixed.md §9: a space between a Han character and an ASCII letter or digit, applied only to
/// the text the shell commits. Never logged (docs/spec/privacy.md §3).
enum CJKSpacing {
    private enum Kind { case han, ascii, other }

    private static func kind(_ c: Character) -> Kind {
        if c.isASCII { return c.isLetter || c.isNumber ? .ascii : .other }  // A-Z a-z 0-9 only
        // The first scalar, like the core's per-scalar is_han: a Han character with a variation selector is still Han.
        if let s = c.unicodeScalars.first, LeftContext.isHan(s) { return .han }
        return .other
    }

    private static func needsSpace(_ a: Kind, _ b: Kind) -> Bool { (a == .han && b == .ascii) || (a == .ascii && b == .han) }

    /// docs/spec/english-mixed.md §9 (英數狀態): whether `c` counts as Han for the spacing rule.
    static func isHan(_ c: Character) -> Bool { kind(c) == .han }

    /// docs/spec/english-mixed.md §9 (英數狀態): apps where a letter typed in the English state may be a command (terminals: vim, less, tmux;
    /// code editors with a Vim mode), so the shell never inserts a space for them and keys pass through as before.
    static let asciiEditors: Set<String> = [
        "com.microsoft.VSCode", "com.microsoft.VSCodeInsiders", "com.todesktop.230313mzl4w4u92", "dev.zed.Zed", "com.apple.dt.Xcode", "md.obsidian",
    ]
    static func asciiExcluded(_ bundle: String) -> Bool {
        LeftContext.terminals.contains(bundle) || asciiEditors.contains(bundle) || bundle.hasPrefix("com.jetbrains.")
    }

    /// `previous` is the character before the insertion point, `nil` when unknown (no leading space then).
    static func apply(previous: Character?, text: String) -> String {
        var out = ""
        var last = previous.map(kind) ?? .other
        for c in text {
            let k = kind(c)
            if needsSpace(last, k) { out.append(" ") }
            out.append(c)
            last = k
        }
        return out
    }
}
