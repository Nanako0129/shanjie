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

  # Also on upgrade: the running old copy must exit so the system starts the new one.
  uninstall on_upgrade: :signal,
            signal:     ["TERM", "com.nyanako.inputmethod.shanjie"]

  zap trash: "~/Library/Preferences/com.nyanako.inputmethod.shanjie.plist"

  caveats <<~EOS
    After the first install, register and enable the input method:
      "$HOME/Library/Input Methods/善解輸入法.app/Contents/MacOS/shanjie" install
    The first install usually needs a log out and log in before macOS accepts
    the input method: if the command exits with 3, log out, log back in, and
    run it again.
  EOS
end
