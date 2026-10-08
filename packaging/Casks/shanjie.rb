cask "shanjie" do
  version "0.0.0"
  sha256 "0000000000000000000000000000000000000000000000000000000000000000"

  url "https://github.com/Nanako0129/shanjie/releases/download/v#{version}/shanjie-#{version}.zip"
  name "善解輸入法"
  name "Shanjie"
  desc "Zhuyin input method that converts whole sentences on the device"
  homepage "https://shanjie.nyanako.com/"

  depends_on arch: :arm64
  depends_on macos: :tahoe

  input_method "善解輸入法.app"

  # Upgrade and reinstall: stop the running old copy only once the new bundle is in place, so the
  # system starts the new one. Homebrew skips `uninstall signal` on upgrade, and `on_upgrade` did
  # not take effect when the installed cask was read back from its receipt (measured 2026-10-08,
  # Homebrew 7.0.7; docs/contracts/s3b.md section 15). The system starts the input method with no
  # arguments; `[^ ]*` keeps a command that merely names this path as an argument from matching
  # (a home directory containing a space is not matched either). An empty prefix covers a
  # system-wide /Library/Input Methods install.
  postflight_steps do
    terminate_process '^[^ ]*/Library/Input Methods/(善解輸入法|shanjie)\.app/Contents/MacOS/shanjie$',
                      match:   :full,
                      notices: ["Stopping any running copy of the input method so the new version is used"]
  end

  uninstall signal: ["TERM", "com.nyanako.inputmethod.shanjie"]

  zap trash: "~/Library/Preferences/com.nyanako.inputmethod.shanjie.plist"

  caveats <<~EOS
    After the first install, register and enable the input method:
      "$HOME/Library/Input Methods/善解輸入法.app/Contents/MacOS/shanjie" install
    If the command exits with 3, macOS has not accepted the input method yet:
    add 善解輸入法 in System Settings > Keyboard > Input Sources (+ → 繁體中文 →
    善解輸入法 → Add).
  EOS
end
