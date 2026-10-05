// Holds secure event input for N seconds, as a password field does, so the input menu's greyed-out
// state can be reproduced without Chrome (docs/research-log.md 2026-10-05). Build: swiftc -O -o
// /tmp/secure-input tools/secure-input.swift; run /tmp/secure-input 60. It turns secure input off when
// the time is up and on Ctrl-C or SIGTERM.
import Carbon
import Foundation

let seconds = Double(CommandLine.arguments.dropFirst().first ?? "60") ?? 60

func finish(_ code: Int32) -> Never {
    print("DisableSecureEventInput:", DisableSecureEventInput(), "on:", IsSecureEventInputEnabled())
    exit(code)
}

var sources: [DispatchSourceSignal] = []
for sig in [SIGINT, SIGTERM] {
    signal(sig, SIG_IGN)
    let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
    source.setEventHandler { finish(128 + sig) }
    source.resume()
    sources.append(source)
}
print("EnableSecureEventInput:", EnableSecureEventInput(), "on:", IsSecureEventInputEnabled())
fflush(stdout)
DispatchQueue.main.asyncAfter(deadline: .now() + seconds) { finish(0) }
dispatchMain()
