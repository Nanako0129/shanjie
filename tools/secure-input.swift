// Holds secure event input for N seconds, as a password field does, so the input menu's greyed-out
// state can be reproduced without Chrome (docs/research-log.md 2026-10-05). Build: swiftc -O -o
// /tmp/secure-input tools/secure-input.swift; run /tmp/secure-input 60. It turns it off at exit.
// Turns secure event input on for N seconds, then off. usage: secure <seconds>
import Carbon
import Foundation
let n = Double(CommandLine.arguments.dropFirst().first ?? "60") ?? 60
print("EnableSecureEventInput:", EnableSecureEventInput(), "on:", IsSecureEventInputEnabled())
fflush(stdout)
Thread.sleep(forTimeInterval: n)
print("DisableSecureEventInput:", DisableSecureEventInput(), "on:", IsSecureEventInputEnabled())
