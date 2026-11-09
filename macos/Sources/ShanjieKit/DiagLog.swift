import AppKit

/// Where the 記錄偶發問題的診斷資料 switch is kept (docs/spec/macos-shell.md §13): `nil` means never chosen, which is on.
@MainActor
public protocol DiagLogStore: AnyObject {
    var diagLog: Bool? { get set }
}

/// The in-memory DiagLogStore.
@MainActor
public final class MemoryDiagLogStore: DiagLogStore {
    public var diagLog: Bool?
    public init(_ diagLog: Bool? = nil) { self.diagLog = diagLog }
}

/// The two pasteboard calls a copy makes (docs/spec/macos-shell.md §13.3); `NSPasteboard` in the app, a recorder in the tests.
@MainActor
public protocol DiagPasteboard: AnyObject {
    @discardableResult func prepareForNewContents(with options: NSPasteboard.ContentsOptions) -> Int
    @discardableResult func setString(_ string: String, forType dataType: NSPasteboard.PasteboardType) -> Bool
}

extension NSPasteboard: DiagPasteboard {}

/// docs/spec/macos-shell.md §13: the diagnostic log of the Shift toggle, kept in this process's memory only. No file, no
/// `Logger`, no `print`: it leaves the process only through `copy(_:to:)`, which the user starts after seeing the text.
/// Every field is an enum, a Bool or a cleaned bundle ID (`clean(_:)`); nothing typed is ever recorded.
@MainActor
public final class DiagLog {
    public enum Event: Equatable {
        /// A Shift release that `ShiftTap` decided on; `right` is keyCode 60.
        case shift(right: Bool, outcome: ShiftTap.Outcome)
        case toggle(bundle: String, english: Bool, hintShown: Bool)
        case activate(bundle: String, owner: Bool, english: Bool)
        case deactivate(bundle: String, owner: Bool, english: Bool)
        case owner(bundle: String)
        case ascii(bundle: String, english: Bool)

        var line: String {
            func lang(_ english: Bool) -> String { english ? "en" : "zh" }
            func own(_ owner: Bool) -> String { owner ? "owner" : "notowner" }
            switch self {
            case .shift(let right, let outcome): return "shift \(right ? "right" : "left") \(outcome.rawValue)"
            case .toggle(let b, let en, let hint): return "toggle \(b) \(lang(en)) \(hint ? "hint" : "nohint")"
            case .activate(let b, let o, let en): return "activate \(b) \(own(o)) \(lang(en))"
            case .deactivate(let b, let o, let en): return "deactivate \(b) \(own(o)) \(lang(en))"
            case .owner(let b): return "owner \(b)"
            case .ascii(let b, let en): return "ascii \(b) \(lang(en))"
            }
        }
    }

    /// The two buffers' sizes: Shift releases are frequent (capitals), so they have their own and cannot push the switches and app
    /// changes out.
    public static let shiftLimit = 200
    public static let otherLimit = 800
    public static let transientType = NSPasteboard.PasteboardType("org.nspasteboard.TransientType")

    private let store: DiagLogStore
    /// A monotonic clock in seconds (the system uptime in the app); a test replaces it.
    var clock: () -> TimeInterval = { ProcessInfo.processInfo.systemUptime }
    private var shifts: [(at: TimeInterval, event: Event)] = []
    private var others: [(at: TimeInterval, event: Event)] = []

    public init(store: DiagLogStore) { self.store = store }

    public var enabled: Bool {
        get { store.diagLog ?? true }
        set {
            store.diagLog = newValue
            if !newValue { shifts = []; others = [] }  // off empties both; on starts from empty
        }
    }

    public var isEmpty: Bool { shifts.isEmpty && others.isEmpty }

    func record(_ event: Event) {
        guard enabled else { return }
        let entry = (at: clock(), event: event)
        if case .shift = event {
            shifts.append(entry)
            if shifts.count > Self.shiftLimit { shifts.removeFirst() }
        } else {
            others.append(entry)
            if others.count > Self.otherLimit { others.removeFirst() }
        }
    }

    /// The bundle ID as recorded: `-` for none, `?` for one that is not 1–255 of `[A-Za-z0-9._-]`.
    static func clean(_ bundle: String?) -> String {
        guard let bundle, !bundle.isEmpty else { return "-" }
        guard bundle.unicodeScalars.count <= 255, bundle.unicodeScalars.allSatisfy(allowed.contains) else { return "?" }
        return bundle
    }
    private static let allowed = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")

    /// The text the user sees and copies: a header line (善解 and macOS versions, the export time), then one line per event, oldest
    /// first, its time as seconds before the export.
    public func export(version: String, now: Date = Date()) -> String {
        let os = ProcessInfo.processInfo.operatingSystemVersion
        let t = clock()
        var lines = ["shanjie \(Self.clean(version)) macOS \(os.majorVersion).\(os.minorVersion).\(os.patchVersion) exported \(ISO8601DateFormatter().string(from: now))"]
        for e in (shifts + others).sorted(by: { $0.at < $1.at }) {
            lines.append(String(format: "-%.3f ", max(0, t - e.at)) + e.event.line)
        }
        return lines.joined(separator: "\n") + "\n"
    }

    /// Puts `text` on `pasteboard` for this Mac only (not Universal Clipboard), marked transient so clipboard managers keep no history.
    public static func copy(_ text: String, to pasteboard: DiagPasteboard) {
        pasteboard.prepareForNewContents(with: .currentHostOnly)
        pasteboard.setString(text, forType: .string)
        pasteboard.setString("", forType: transientType)
    }
}
