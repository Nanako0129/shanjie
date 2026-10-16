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

/// How a request to start Sparkle's information-only check went (answered by the app's `Updater`).
public enum ProbeStart: Sendable {
    /// The check runs and will report back through `Updates.finished`.
    case started
    /// A Sparkle session is already running (Sparkle would ignore the call and never report back): nothing happens.
    case busy
    /// The updater is not running (it did not start, or this build does not start it): the check fails.
    case unavailable
}

/// The update state shared by the 關於 page and the input menu (contract sections 1 and 2). Sparkle stays in the app target
/// (`Updater`), which sets `probe` and `interactive` and reports each finished check through `finished`; the decisions (when
/// the background check runs, when the interactive flow may start, what the page and the menu show) are here, so the tests
/// reach them without Sparkle or the network.
@MainActor
public final class Updates: ObservableObject {
    /// At most one background check a day (contract section 1).
    public nonisolated static let interval: TimeInterval = 24 * 60 * 60

    /// The Info.plist key scripts/build-app.sh writes: `release` for the release workflow's build, `test` for an update test
    /// build, `local` for every other build. Only `release` and `test` start the updater, so a copy built and installed
    /// locally never reads the production feed.
    public nonisolated static let buildKey = "ShanjieUpdateBuild"

    /// The updater starts only when the process runs from an app bundle (never in `swift test` or a bare executable, which
    /// have no Info.plist with the feed and the key) built as a release or an update test build (`buildKey`).
    public nonisolated static func runsUpdater(bundleURL: URL, build: String?) -> Bool {
        bundleURL.pathExtension == "app" && (build == "release" || build == "test")
    }

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
    /// A check started and has not reported back yet. The 立即檢查 and 更新到 X buttons and the menu item are disabled meanwhile.
    @Published public private(set) var checking = false

    /// Starts Sparkle's information-only check (no window).
    public var probe: () -> ProbeStart = { .unavailable }
    /// Sparkle's interactive flow (download progress, notes, install and relaunch). Called only from `finished`, after a
    /// silent check the user started with `install` confirmed a newer version.
    public var interactive: () -> Void = {}
    /// Brings the open Sparkle windows to the front: 更新到 X or the menu item pressed while a Sparkle session runs.
    public var focus: () -> Void = {}

    private let store: UpdateStore
    /// `install` started the running check: open the interactive flow if it finds a newer version.
    private var installAfterCheck = false

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

    /// The background check, called by the app's timer: checks only when `shouldCheck` says so; never records a failure.
    public func tick(now: Date = Date()) {
        guard Self.shouldCheck(enabled: autoCheck, lastCheck: lastCheck, now: now) else { return }
        _ = begin(now)
    }

    /// 立即檢查: always connects, whatever 自動檢查更新 says (contract section 1). Ignored while a check runs; an updater that is
    /// not running reads 檢查失敗 with the date.
    public func checkNow(now: Date = Date()) {
        if begin(now) == .unavailable { failNow(now) }
    }

    /// 更新到 X and the menu item: a silent check first, and Sparkle's interactive flow only if it confirms a newer version
    /// (`finished`). Otherwise the state says 已是最新 or 檢查失敗 and no Sparkle window opens. While a session runs, the press
    /// brings Sparkle's windows back to the front instead: the input method has no Dock icon to do it (device check 2026-10-16).
    public func install(now: Date = Date()) {
        switch begin(now) {
        case .started: installAfterCheck = true
        case .busy: focus()
        case .unavailable: failNow(now)
        }
    }

    /// The start time is recorded only when a check really started, and counts for the daily limit whatever its result.
    private func begin(_ now: Date) -> ProbeStart {
        guard !checking else { return .busy }
        let s = probe()
        if s == .started {
            checking = true
            lastCheck = now
            store.lastCheck = now
        }
        return s
    }

    private func failNow(_ now: Date) {
        lastCheck = now
        store.lastCheck = now
        finished(.failed)
    }

    /// A check ended. A version already known stays known when a check fails or when a finished cycle reports a newer version
    /// without saying which; `nil` only ends the check (an interactive session the user dismissed).
    public func finished(_ r: UpdateResult?, version newer: String? = nil) {
        checking = false
        let wantsInstall = installAfterCheck
        installAfterCheck = false
        guard let r else { return }
        result = r
        switch r {
        case .available: if let newer { available = newer }
        case .upToDate: available = nil
        case .failed: break
        }
        var s = ["by": version, "result": r.rawValue]
        if let available { s["available"] = available }
        store.status = s
        if wantsInstall, r == .available, available != nil { interactive() }
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
