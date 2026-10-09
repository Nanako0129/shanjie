import Combine
import Foundation

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
    @Published public private(set) var demote = true
    @Published public private(set) var acgPack = true
    @Published public private(set) var backupExcluded = false
    @Published public private(set) var glassTint = 0.0
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
        demote = shell.demoteOn
        acgPack = shell.acgPackOn
        glassTint = shell.glassTint
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
    public func setDemote(_ on: Bool) { shell.applyDemote(on) }
    public func setAcgPack(_ on: Bool) { shell.setAcgPack(on) }
    public func setBackupExcluded(_ on: Bool) {
        shell.setBackupExcluded(on)
        refreshExternal()  // not read on the change notification
    }
    public func setGlassTint(_ v: Double) { shell.setGlassTint(v) }
    /// The same confirmation window as the menu's.
    public func clear() {
        shell.confirmAndClear { [weak self] in self?.refreshExternal() }  // the "cannot save" row may change
    }
}
