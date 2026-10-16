import Combine
import Foundation

/// The result of the last update check (docs/contracts/about-update.md section 1), shown on the 關於 page.
public enum UpdateResult: String, Equatable, Sendable {
    case upToDate, available, failed
}

/// Where the update settings and the last check live (contract section 2): the app's UserDefaults domain in the input method
/// (`DefaultsUpdateStore` in the app target), memory in the tests.
@MainActor
public protocol UpdateStore: AnyObject {
    /// 自動檢查更新, key `updateAutoCheck`; absent means on.
    var autoCheck: Bool? { get set }
    /// When the last check started, key `updateLastCheck`.
    var lastCheck: Date? { get set }
    /// The last result, key `updateStatus`: `by` (the version that checked), `result` (an `UpdateResult`), `available` (the
    /// newer version, when known).
    var status: [String: String]? { get set }
}

@MainActor
public final class MemoryUpdateStore: UpdateStore {
    public var autoCheck: Bool?
    public var lastCheck: Date?
    public var status: [String: String]?
    public init() {}
}

/// The update state shared by the 關於 page and the input menu (contract sections 1 and 2). Sparkle stays in the app target
/// (`Updater`), which sets `probe` and `install` and reports each finished check through `finished`; the decisions (when the
/// background check runs, what the page and the menu show) are here, so the tests reach them without Sparkle or the network.
@MainActor
public final class Updates: ObservableObject {
    /// At most one background check a day (contract section 1).
    public nonisolated static let interval: TimeInterval = 24 * 60 * 60

    /// The updater starts only when the process runs from an app bundle: never in `swift test` or a bare executable, which
    /// have no Info.plist with the feed and the key.
    public nonisolated static func runsUpdater(bundleURL: URL) -> Bool { bundleURL.pathExtension == "app" }

    /// `CFBundleShortVersionString`; `0.0.0` (a dispatched dry run, a local build without a tag) and a missing value read 開發版.
    public nonisolated static func versionText(_ version: String?) -> String {
        guard let version, !version.isEmpty, version != "0.0.0" else { return "開發版" }
        return version
    }

    /// The background check's decision: only with 自動檢查更新 on, and only when no check started in the last day. A last check
    /// in the future (the clock was set back) does not hold checks off until the clock catches up.
    public nonisolated static func shouldCheck(enabled: Bool, lastCheck: Date?, now: Date) -> Bool {
        guard enabled else { return false }
        guard let lastCheck else { return true }
        let elapsed = now.timeIntervalSince(lastCheck)
        return elapsed >= interval || elapsed < 0
    }

    public let version: String
    @Published public private(set) var autoCheck: Bool
    @Published public private(set) var lastCheck: Date?
    @Published public private(set) var result: UpdateResult?
    /// The newer version Sparkle found, `nil` when none is known. The 更新到 X button and the menu item exist only while set.
    @Published public private(set) var available: String?
    /// A check started and has not reported back yet.
    @Published public private(set) var checking = false

    /// Starts Sparkle's information-only check (no window); false when it could not start (the updater did not start, or a
    /// session is already running, in which case Sparkle would ignore the call and never report back).
    public var probe: () -> Bool = { false }
    /// Sparkle's interactive flow (download progress, notes, install and relaunch); only ever on the user's press.
    public var install: () -> Void = {}

    private let store: UpdateStore

    public init(store: UpdateStore, version: String) {
        self.store = store
        self.version = version
        autoCheck = store.autoCheck ?? true
        lastCheck = store.lastCheck
        // A result recorded by another version (the user updated, or installed another way) says nothing about this one.
        if let s = store.status, s["by"] == version, let r = s["result"].flatMap(UpdateResult.init(rawValue:)) {
            result = r
            available = s["available"]
        } else {
            store.status = nil
        }
    }

    public var versionText: String { Self.versionText(version) }

    public func setAutoCheck(_ on: Bool) {
        autoCheck = on
        store.autoCheck = on
    }

    /// The background check, called by the app's timer: checks only when `shouldCheck` says so.
    public func tick(now: Date = Date()) {
        guard Self.shouldCheck(enabled: autoCheck, lastCheck: lastCheck, now: now) else { return }
        _ = start(now)
    }

    /// 立即檢查: always connects, whatever 自動檢查更新 says (contract section 1).
    public func checkNow(now: Date = Date()) {
        if !start(now) { finished(.failed) }
    }

    /// The start time is recorded only when a check really started, and counts for the daily limit whatever its result.
    private func start(_ now: Date) -> Bool {
        guard !checking, probe() else { return false }
        checking = true
        lastCheck = now
        store.lastCheck = now
        return true
    }

    /// A check ended. `available` keeps a version already known when a check fails; `nil` only ends the check (an interactive
    /// session the user dismissed).
    public func finished(_ r: UpdateResult?, version newer: String? = nil) {
        checking = false
        guard let r else { return }
        result = r
        switch r {
        case .available: available = newer
        case .upToDate: available = nil
        case .failed: break
        }
        var s = ["by": version, "result": r.rawValue]
        if let available { s["available"] = available }
        store.status = s
    }
}

/// Fixed strings of the 關於 page and the update menu item.
public enum UpdateText {
    public static let upToDate = "已是最新"
    public static func available(_ v: String) -> String { "有新版本 \(v)" }
    public static let failed = "檢查失敗"
    public static let checking = "檢查中…"
    public static func install(_ v: String) -> String { "更新到 \(v)" }
    public static func menu(_ v: String) -> String { "有新版本 \(v)，按此更新…" }

    public static func result(_ r: UpdateResult?, available: String?) -> String? {
        switch r {
        case .upToDate?: upToDate
        case .available?: available.map(Self.available) ?? upToDate
        case .failed?: failed
        case nil: nil
        }
    }
}
