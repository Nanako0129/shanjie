/// docs/contracts/cjk-spacing.md section 1: a space between a Han character and an ASCII letter or digit, applied only to
/// the text the shell commits. Never logged (s3b R2).
enum CJKSpacing {
    private enum Kind { case han, ascii, other }

    private static func kind(_ c: Character) -> Kind {
        if c.isASCII { return c.isLetter || c.isNumber ? .ascii : .other }  // A-Z a-z 0-9 only
        // The first scalar, like the core's per-scalar is_han: a Han character with a variation selector is still Han.
        if let s = c.unicodeScalars.first, LeftContext.isHan(s) { return .han }
        return .other
    }

    private static func needsSpace(_ a: Kind, _ b: Kind) -> Bool { (a == .han && b == .ascii) || (a == .ascii && b == .han) }

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
