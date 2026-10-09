import AppKit
import ShanjieKit
import SwiftUI

/// The settings window (docs/contracts/settings-window.md section 2.1): one NSWindow with a SwiftUI
/// grouped Form, in the input method's own process. It has no text field, so it needs no Edit menu and
/// the input method never types into its own window. Brought forward like `AlertDialogs`: remember the
/// frontmost app, `NSApp.activate()`, `makeKeyAndOrderFront`; on close the focus goes back.
@MainActor
final class SettingsWindowController: NSObject, NSWindowDelegate {
    private let shell: Shell
    /// Built on the first `show()`, so a user who never opens the window pays nothing.
    private var model: SettingsModel?
    private var window: NSWindow?
    private var previous: NSRunningApplication?

    init(shell: Shell) {
        self.shell = shell
    }

    func show() {
        if let front = NSWorkspace.shared.frontmostApplication, front != NSRunningApplication.current {
            previous = front
        }
        let m = model ?? SettingsModel(shell: shell)
        model = m
        m.refreshExternal()
        let w = window ?? makeWindow(m)
        window = w
        NSApp.activate()
        w.makeKeyAndOrderFront(nil)
        // activate() is only a request (cooperative activation, macOS 14+); on the user's machine it was
        // refused and the window stayed behind the frontmost app (device check 2026-10-10). Like vChewing,
        // put it in front regardless; a click on it then activates the input method.
        w.orderFrontRegardless()
    }

    private func makeWindow(_ model: SettingsModel) -> NSWindow {
        let w = NSWindow(contentViewController: NSHostingController(rootView: SettingsForm(model: model)))
        w.title = "善解設定"
        w.styleMask = [.titled, .closable]
        w.isReleasedWhenClosed = false
        // Above ordinary app windows even when the input method is not active, as AlertDialogs does with
        // .modalPanel (contract section 2.1, device check 2026-10-10).
        w.level = .floating
        w.delegate = self
        w.center()
        return w
    }

    /// The secure-input and cannot-save rows have no notification, so they are re-read each time the window is key.
    func windowDidBecomeKey(_ notification: Notification) {
        model?.refreshExternal()
    }

    func windowWillClose(_ notification: Notification) {
        // Only while we are still the active app: otherwise the user has moved on and this would steal focus.
        if NSApp.isActive { previous?.activate() }
        previous = nil
    }
}

/// System controls only. Order follows the menu (section 2.2); each control writes through `SettingsModel`.
private struct SettingsForm: View {
    /// Chosen to fit the controls and their labels on one line each; not measured against any reference.
    private static let formWidth: CGFloat = 420
    @ObservedObject var model: SettingsModel

    var body: some View {
        Form {
            Section("鍵盤") {
                Picker("鍵盤排列", selection: Binding(get: { model.layout }, set: { model.selectLayout($0) })) {
                    Text("標準").tag(InputMode.standard)
                    Text("倚天").tag(InputMode.eten)
                }
            }
            Section("選字") {
                Toggle(MenuEntry.Text.prediction, isOn: Binding(get: { model.prediction }, set: { model.setPrediction($0) }))
                Toggle(MenuEntry.Text.demote, isOn: Binding(get: { model.demote }, set: { model.setDemote($0) }))
                Toggle(MenuEntry.Text.acgPack, isOn: Binding(get: { model.acgPack }, set: { model.setAcgPack($0) }))
            }
            Section("外觀") {
                Slider(value: Binding(get: { model.glassTint }, set: { model.setGlassTint($0) }), in: 0...1) {
                    Text("候選窗玻璃深淺")
                } minimumValueLabel: {
                    Text("透明")
                } maximumValueLabel: {
                    Text("深")
                }
            }
            Section("選字記憶") {
                if model.pausedSecure { Text(MenuEntry.Text.pausedSecure).foregroundStyle(.secondary) }
                if model.unavailable { Text(MenuEntry.Text.unavailable).foregroundStyle(.secondary) }
                Toggle(MenuEntry.Text.excludeBackup, isOn: Binding(get: { model.backupExcluded }, set: { model.setBackupExcluded($0) }))
                Button(MenuEntry.Text.clear) { model.clear() }
            }
        }
        .formStyle(.grouped)
        .frame(width: Self.formWidth)
        .fixedSize(horizontal: false, vertical: true)
    }
}
