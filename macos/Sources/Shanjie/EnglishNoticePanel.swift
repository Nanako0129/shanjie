import AppKit
import ShanjieKit
import SwiftUI

/// The explanation window of the English defaults change (docs/spec/english-mixed.md §10.3): a small panel of the
/// input method's own, nonactivating and never key like `PanelWindow`, so the typing app keeps the keyboard focus, while its
/// buttons and close button still take clicks. It places itself with `PanelPlacement.noticeOrigin`, away from the caret. System
/// controls and the system's default window animation.
@MainActor
final class EnglishNoticeAdapter: NSObject, EnglishNoticePanel, NSWindowDelegate {
    /// The text column's width; a round first value for the device rounds, not measured.
    private static let width: CGFloat = 400
    private lazy var window: PanelWindow = {
        let w = PanelWindow(contentRect: .zero, styleMask: [.titled, .closable, .nonactivatingPanel], backing: .buffered, defer: true)
        w.title = "善解"
        w.level = .floating
        w.hidesOnDeactivate = false
        w.isReleasedWhenClosed = false
        w.delegate = self
        return w
    }()
    private var done: (@MainActor (Bool) -> Void)?

    var isShown: Bool { window.isVisible }

    func show(caret: NSRect?, done: @escaping @MainActor (Bool) -> Void) {
        self.done = done
        let host = FirstMouseHostingView(rootView: EnglishNoticeView(
            width: Self.width,
            onOpenSettings: { [weak self] in self?.finish(openSettings: true) },
            onAcknowledge: { [weak self] in self?.finish(openSettings: false) }))
        window.contentView = host
        window.setContentSize(host.fittingSize)
        let screens = NSScreen.screens.map { PanelPlacement.Screen(frame: $0.frame, visibleFrame: $0.visibleFrame) }
        guard !screens.isEmpty else { return }
        let main = NSScreen.main.flatMap { m in NSScreen.screens.firstIndex(of: m) } ?? 0
        window.setFrameOrigin(PanelPlacement.noticeOrigin(caret: caret, size: window.frame.size, screens: screens, main: main))
        window.orderFrontRegardless()
    }

    func hide() { window.orderOut(nil) }

    /// The close button: the same as 知道了. `hide()` uses `orderOut`, which does not come through here.
    func windowWillClose(_ notification: Notification) { finish(openSettings: false) }

    private func finish(openSettings: Bool) {
        guard let done else { return }
        self.done = nil
        done(openSettings)
    }
}

private struct EnglishNoticeView: View {
    let width: CGFloat
    let onOpenSettings: () -> Void
    let onAcknowledge: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(EnglishNoticeText.title).font(.headline)
            Text(EnglishNoticeText.intro)
            Text(EnglishNoticeText.overwrite)
            Text(EnglishNoticeText.shift)
            Text(EnglishNoticeText.how).foregroundStyle(.secondary)
            HStack {
                Spacer()
                Button(EnglishNoticeText.openSettings, action: onOpenSettings)
                Button(EnglishNoticeText.acknowledge, action: onAcknowledge)
            }
        }
        .fixedSize(horizontal: false, vertical: true)
        .padding(20)
        .frame(width: width)
    }
}

/// The panel can never become key and the app is never active, so the first click on a button must count (as the candidate cells do,
/// CandidateCells.swift `acceptsFirstMouse`); otherwise 知道了 could need two clicks and the owed flag would stay.
private final class FirstMouseHostingView<Content: View>: NSHostingView<Content> {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}
