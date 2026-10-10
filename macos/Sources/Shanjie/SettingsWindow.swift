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
    private var host: NSHostingView<SettingsForm>?
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
        if let host {
            let fit = host.fittingSize
            w.setContentSize(NSSize(width: fit.width, height: fit.height + Self.titleBarHeight))
        }
        center(w)
        NSApp.activate()
        w.makeKeyAndOrderFront(nil)
        // activate() is only a request (cooperative activation, macOS 14+); on the user's machine it was
        // refused and the window stayed behind the frontmost app (device check 2026-10-10). Like vChewing,
        // put it in front regardless; a click on it then activates the input method.
        w.orderFrontRegardless()
    }

    /// The window's background material (user, 2026-10-10: Liquid Glass was too see-through; a flatter glass).
    /// `.popover` was picked by eye on the system's list, not measured; the user's device check passed (2026-10-10).
    static let backgroundMaterial: NSVisualEffectView.Material = .popover

    /// The glass runs under the title bar, as in Syrtis's settings window (TokenBar SettingsWindowController.swift):
    /// with the window's background clear, a title bar outside the glass was see-through (user, 2026-10-10, third
    /// visual round). The form starts this far down. The mask here deliberately leaves out the window's
    /// `.fullSizeContentView`: with it the content rect is the whole frame and this comes out 0. 32 pt on the maintainer's
    /// machine (2026-10-10 review probe); a toolbar or another title bar style would need this changed.
    static let titleBarHeight = NSWindow.frameRect(forContentRect: NSRect(x: 0, y: 0, width: 100, height: 100), styleMask: [.titled, .closable]).height - 100

    private func makeWindow(_ model: SettingsModel) -> NSWindow {
        // The system's frosted material behind the content, never around it (a wrapped view gets vibrancy
        // and washed-out text; Syrtis, PR 491); the SwiftUI form sits on top with its own background hidden.
        // Not Liquid Glass (NSGlassEffectView): on device it was too see-through for a settings window, and the
        // user asked for a flatter glass (2026-10-10, third visual round). The tint preview keeps the real
        // candidate glass; whether its depth on this backdrop matches the bar over an app was not measured.
        let glass = NSVisualEffectView()
        glass.material = Self.backgroundMaterial
        glass.blendingMode = .behindWindow
        glass.state = .active
        let host = NSHostingView(rootView: SettingsForm(model: model))
        host.safeAreaRegions = []  // placed below the title bar by its top constraint, so no second inset
        self.host = host
        let container = NSView()
        let layers: [(NSView, CGFloat)] = [(glass, 0), (host, Self.titleBarHeight)]
        for (v, top) in layers {
            v.translatesAutoresizingMaskIntoConstraints = false
            container.addSubview(v)
            NSLayoutConstraint.activate([
                v.leadingAnchor.constraint(equalTo: container.leadingAnchor), v.trailingAnchor.constraint(equalTo: container.trailingAnchor),
                v.topAnchor.constraint(equalTo: container.topAnchor, constant: top), v.bottomAnchor.constraint(equalTo: container.bottomAnchor),
            ])
        }
        let w = NSWindow(contentRect: .zero, styleMask: [.titled, .closable, .fullSizeContentView], backing: .buffered, defer: false)
        w.titlebarAppearsTransparent = true
        w.contentView = container
        w.title = "善解設定"
        w.isOpaque = false
        w.backgroundColor = .clear
        w.isReleasedWhenClosed = false
        // Above ordinary app windows even when the input method is not active, as AlertDialogs does with
        // .modalPanel (contract section 2.1, device check 2026-10-10).
        w.level = .floating
        w.delegate = self
        return w
    }

    /// Centred on the screen the mouse is on: the user just clicked the menu there, and `NSScreen.main`
    /// is the screen of the key window, which an input method's process does not have. Done after the
    /// content size is set (`center()` before layout put the window at the top, device check 2026-10-10).
    private func center(_ w: NSWindow) {
        let mouse = NSEvent.mouseLocation
        guard let screen = NSScreen.screens.first(where: { NSMouseInRect(mouse, $0.frame, false) }) ?? NSScreen.main else { return }
        let area = screen.visibleFrame
        w.setFrameOrigin(NSPoint(x: area.midX - w.frame.width / 2, y: area.midY - w.frame.height / 2))
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
struct SettingsForm: View {
    /// Chosen to fit the controls and their labels on one line each; not measured against any reference.
    private static let formWidth: CGFloat = 420
    /// Icon-to-slider gap, Syrtis's value (TokenBar GlassTintControl.swift `HStack(spacing: 8)`); not measured.
    private static let tintIconSpacing: CGFloat = 8
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
                // Greyed while the prediction row is off, like the menu item (v3-engine section 12.1).
                Toggle(MenuEntry.Text.abbreviation, isOn: Binding(get: { model.abbreviation }, set: { model.setAbbreviation($0) }))
                    .disabled(!model.prediction)
                Toggle(MenuEntry.Text.demote, isOn: Binding(get: { model.demote }, set: { model.setDemote($0) }))
                Toggle(MenuEntry.Text.acgPack, isOn: Binding(get: { model.acgPack }, set: { model.setAcgPack($0) }))
                if let line = model.acgDataLine {
                    Text(line).font(.caption).foregroundStyle(.secondary)
                }
            }
            Section("外觀") {
                // Before the glass tint (candidate-vertical contract section 2.1). A candidate window already open keeps its orientation, so the next one uses the new setting. (The core also turns a prediction row on screen, but a click in this window is inferred, not measured, to commit the composition and hide the panel first.)
                Picker("候選窗方向", selection: Binding(get: { model.candidateVertical }, set: { model.setCandidateVertical($0) })) {
                    Text("橫排").tag(false)
                    Text("直排").tag(true)
                }
                .pickerStyle(.segmented)
                // The end icons are Syrtis's (TokenBar GlassTintControl.swift): outline at the clear end, filled at the deep
                // end, in an HStack beside the slider. As the Slider's own value labels inside a grouped Form both came out
                // filled on device (user, 2026-10-10). The slider keeps the regular size (Syrtis's is .small), as checked on
                // device. The icons are decoration, hidden from VoiceOver; the slider carries the label.
                LabeledContent("候選窗玻璃深淺") {
                    HStack(spacing: Self.tintIconSpacing) {
                        Image(systemName: "rectangle.on.rectangle")
                            .foregroundStyle(.secondary)
                            .accessibilityHidden(true)
                        Slider(value: Binding(get: { model.glassTint }, set: { model.setGlassTint($0) }), in: 0...1)
                            .accessibilityLabel("候選窗玻璃深淺")
                        Image(systemName: "rectangle.fill.on.rectangle.fill")
                            .foregroundStyle(.secondary)
                            .accessibilityHidden(true)
                    }
                }
                // The real bar's glass, cells and tint decision (the depth over an app while typing was not compared).
                HStack {
                    Spacer()
                    TintPreview(value: model.glassTint)
                    Spacer()
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
        .scrollContentBackground(.hidden)  // the window's glass shows through
        .frame(width: Self.formWidth)
        .fixedSize(horizontal: false, vertical: true)
    }
}

/// `SampleCandidateBar` in SwiftUI; the slider value goes in on every update, so it follows a drag.
private struct TintPreview: NSViewRepresentable {
    let value: Double

    func makeNSView(context: Context) -> SampleCandidateBar { SampleCandidateBar() }
    func updateNSView(_ bar: SampleCandidateBar, context: Context) { bar.setTint(value) }
}
