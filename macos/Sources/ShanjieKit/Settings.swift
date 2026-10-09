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

    @Published public private(set) var layout: InputMode
    @Published public private(set) var prediction: Bool
    @Published public private(set) var demote: Bool
    @Published public private(set) var acgPack: Bool
    @Published public private(set) var backupExcluded: Bool
    @Published public private(set) var glassTint: Double
    /// Status rows (section 2.2). There is no "this app" row: it comes from one session's client and
    /// this window belongs to no session.
    @Published public private(set) var pausedSecure: Bool
    @Published public private(set) var unavailable: Bool

    public init(shell: Shell) {
        self.shell = shell
        layout = shell.layout
        prediction = shell.predictionOn
        demote = shell.demoteOn
        acgPack = shell.acgPackOn
        backupExcluded = shell.backupExcluded
        glassTint = shell.glassTint
        pausedSecure = shell.isSecureInput()
        unavailable = shell.learningUnavailable
        observer = NotificationCenter.default.addObserver(forName: Shell.didChangeSettings, object: shell, queue: .main) {
            [weak self] _ in MainActor.assumeIsolated { self?.refresh() }
        }
    }

    isolated deinit {
        if let observer { NotificationCenter.default.removeObserver(observer) }
    }

    /// Re-reads everything; also called when the window opens (the secure-input state has no notification).
    public func refresh() {
        layout = shell.layout
        prediction = shell.predictionOn
        demote = shell.demoteOn
        acgPack = shell.acgPackOn
        backupExcluded = shell.backupExcluded
        glassTint = shell.glassTint
        pausedSecure = shell.isSecureInput()
        unavailable = shell.learningUnavailable
    }

    public func selectLayout(_ m: InputMode) { shell.selectLayout(m) }
    public func setPrediction(_ on: Bool) { shell.applyPrediction(on) }
    public func setDemote(_ on: Bool) { shell.applyDemote(on) }
    public func setAcgPack(_ on: Bool) { shell.setAcgPack(on) }
    public func setBackupExcluded(_ on: Bool) { shell.setBackupExcluded(on) }
    public func setGlassTint(_ v: Double) { shell.setGlassTint(v) }
    /// The same confirmation window as the menu's.
    public func clear() {
        shell.dialogs.confirmClear { [weak shell] clear in
            if clear { shell?.clearLearning() }
        }
    }
}
