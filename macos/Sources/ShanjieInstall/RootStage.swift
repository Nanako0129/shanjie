import Foundation
import Security

/// docs/contracts/system-install.md section 2.3: the root stage of "This Mac". A constant command,
/// built here and run by the installer through `do shell script … with administrator privileges`;
/// it runs nothing from the installer's bundle (which sits where the user can write). The one
/// variable is the installer's own extraction folder, checked against `isExpectedPath` and single
/// quoted. Pure: the tests run the built command with /bin/sh against a temporary root they own.
public enum RootStage {
    public static let systemFolder = "/Library/Input Methods"
    public static let appName = "善解輸入法.app"
    /// The requirement the copy must satisfy: the shipping ID, signed by this project's team with a
    /// Developer ID Application certificate. The two marker fields (the Developer ID intermediate
    /// CA, 1.2.840.113635.100.6.2.6, and the Developer ID Application leaf, …6.1.13) are hardening
    /// from code review: the team's OU alone also accepts its Apple Development and Mac App Store
    /// certificates.
    public static let requirement =
        #"anchor apple generic and identifier "com.nyanako.inputmethod.shanjie" and certificate 1[field.1.2.840.113635.100.6.2.6] exists and certificate leaf[field.1.2.840.113635.100.6.1.13] exists and certificate leaf[subject.OU] = "2LJ882GPY8""#

    /// Removes this Mac's copy (section 2.1), shown when "My Account Only" is blocked; the same line
    /// install-ime.sh prints and the README gives.
    public static let removalCommand = #"sudo rm -rf "/Library/Input Methods/善解輸入法.app" "/Library/Input Methods/.shanjie-previous""#

    public struct Parameters: Equatable, Sendable {
        /// The input methods folder the copy goes into.
        public var root: String
        /// Owner of the copy, and the uid the existing folder must belong to (step 1).
        public var ownerUID: UInt32
        public var ownerGID: UInt32
        public var requirement: String
        /// The CFBundleVersion the copy must carry, compiled into the installer executable.
        public var version: String

        public init(root: String, ownerUID: UInt32, ownerGID: UInt32, requirement: String, version: String) {
            self.root = root
            self.ownerUID = ownerUID
            self.ownerGID = ownerGID
            self.requirement = requirement
            self.version = version
        }

        /// The values that ship: /Library/Input Methods, root:wheel (0:0), the Developer ID requirement.
        public static func production(version: String) -> Parameters {
            Parameters(root: systemFolder, ownerUID: 0, ownerGID: 0, requirement: RootStage.requirement, version: version)
        }
    }

    public enum BuildError: Error, Equatable {
        /// The extraction folder holds a character outside `isExpectedPath`.
        case unexpectedPath
        /// The expected version is missing or not dotted numbers.
        case badVersion
    }

    /// The prefix every command starts with: `env -i` clears the environment of the one command
    /// after it, so everything (including `umask`, a shell builtin) runs inside `sh -c`.
    public static let commandPrefix = "/usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin /bin/sh -c "

    /// Absolute, no `.` or `..` component, and only letters, digits (any script, so the tests can
    /// use full-width names), space and `/._-`: nothing either quoting layer treats specially.
    public static func isExpectedPath(_ path: String) -> Bool {
        let allowed = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "/._- "))
        return path.hasPrefix("/") && !path.isEmpty
            && path.unicodeScalars.allSatisfy(allowed.contains)
            && !path.split(separator: "/").contains { $0 == "." || $0 == ".." }
    }

    static func isVersion(_ v: String) -> Bool {
        !v.isEmpty && v.split(separator: ".", omittingEmptySubsequences: false).allSatisfy { !$0.isEmpty && $0.allSatisfy(\.isASCII) && $0.allSatisfy(\.isNumber) }
    }

    /// POSIX shell single quotes.
    public static func shellQuote(_ s: String) -> String { "'" + s.replacingOccurrences(of: "'", with: #"'\''"#) + "'" }

    /// An AppleScript string literal: `\` and `"` escaped.
    public static func appleScriptLiteral(_ s: String) -> String {
        "\"" + s.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"") + "\""
    }

    /// The shell script, steps 1 to 7. `source` is the folder holding the extracted 善解輸入法.app.
    /// The staging folder stays as mktemp made it (0700, root's) until it is removed; owner and
    /// modes are set on the copy inside it only. Nothing that follows a symbolic link runs on the
    /// copy before its signature is verified (verifier finding: `chmod -RN` follows a link one
    /// level, so a planted link made root strip ACLs outside the bundle even when the bundle was
    /// then refused): `chown -R` and `chmod -R <mode>` do not follow links; `--strict` refuses
    /// links that point out of the bundle; the ACLs are then removed per entry, links skipped. The old .shanjie-previous is moved into the staging
    /// folder, not deleted, and is deleted with it only once the new copy is in place.
    static func script(source: String, _ p: Parameters) -> String {
        """
        set -u
        umask 022
        fail() { echo "root stage: $*" >&2; exit 1; }
        SRC=\(shellQuote(source))
        R=\(shellQuote(p.root))
        OWNER=\(shellQuote("\(p.ownerUID):\(p.ownerGID)"))
        OWNER_UID=\(shellQuote(String(p.ownerUID)))
        REQ=\(shellQuote(p.requirement))
        WANT=\(shellQuote(p.version))
        N=\(shellQuote(appName))
        D="$R/$N"
        P="$R/.shanjie-previous"
        S=
        trap '[ -z "$S" ] || /bin/rm -rf "$S"' EXIT
        # Puts the kept previous version back from the staging folder. If that fails, the staging
        # folder (holding it) is left in place instead of being removed.
        restore() {
          [ -e "$S/previous" ] || [ -L "$S/previous" ] || return 0
          /bin/mv "$S/previous" "$P" || { echo "root stage: the previous version is left in $S/previous" >&2; S=; }
        }
        atleast() {
          a=$1; b=$2
          while [ -n "$a" ] || [ -n "$b" ]; do
            x=${a%%.*}; y=${b%%.*}
            case $a in *.*) a=${a#*.} ;; *) a= ;; esac
            case $b in *.*) b=${b#*.} ;; *) b= ;; esac
            [ "${x:-0}" -gt "${y:-0}" ] && return 0
            [ "${x:-0}" -lt "${y:-0}" ] && return 1
          done
          return 0
        }
        if [ -e "$R" ] || [ -L "$R" ]; then
          [ -d "$R" ] && [ ! -L "$R" ] || fail "$R is not a directory"
          [ "$(/usr/bin/stat -f %u "$R")" = "$OWNER_UID" ] || fail "$R is not owned by uid $OWNER_UID"
          m=$(/usr/bin/stat -f %Lp "$R") || fail "cannot read the mode of $R"
          case $m in [0-7][0-7][0-7]|[0-7][0-7][0-7][0-7]) ;; *) fail "unexpected mode $m of $R" ;; esac
          [ $(( 0$m & 022 )) -eq 0 ] || fail "$R is writable by its group or others"
        else
          /bin/mkdir -m 755 "$R" && /usr/sbin/chown "$OWNER" "$R" || fail "cannot create $R"
        fi
        S=$(/usr/bin/mktemp -d "$R/.shanjie-staging.XXXXXX") || { S=; fail "cannot create a staging folder"; }
        /usr/bin/ditto --noqtn --noacl "$SRC/$N" "$S/$N" || fail "copy failed"
        /usr/sbin/chown -R "$OWNER" "$S/$N" && /bin/chmod -R u+rwX,go+rX,go-w,ug-s "$S/$N" || fail "cannot set owner and modes"
        /usr/bin/codesign --verify --strict --deep -R="$REQ" "$S/$N" || fail "the copy does not satisfy the signing requirement"
        /usr/bin/find "$S/$N" ! -type l -exec /bin/chmod -N {} + || fail "cannot remove ACLs"
        L=$(/bin/ls -leR "$S/$N") || fail "cannot list the copy"
        if printf '%s\\n' "$L" | /usr/bin/grep -q '^ *[0-9][0-9]*: '; then fail "the copy carries an ACL"; fi
        V=$(/usr/bin/plutil -extract CFBundleVersion raw "$S/$N/Contents/Info.plist") || fail "cannot read the version of the copy"
        [ "$V" = "$WANT" ] || fail "the copy is version $V, not $WANT"
        [ ! -L "$D" ] || fail "$D is a symbolic link"
        if [ -e "$D" ]; then
          I=$(/usr/bin/plutil -extract CFBundleVersion raw "$D/Contents/Info.plist") || fail "cannot read the version of $D"
          case $I in ''|.*|*.|*..*|*[!0-9.]*) fail "unexpected version $I of $D" ;; esac
          atleast "$V" "$I" || fail "$D is version $I, newer than $V"
          if [ -e "$P" ] || [ -L "$P" ]; then /bin/mv "$P" "$S/previous" || fail "cannot set $P aside"; fi
          if ! /bin/mv "$D" "$P"; then restore; fail "cannot keep the previous version"; fi
          if ! /bin/mv "$S/$N" "$D"; then /bin/mv "$P" "$D" && restore; fail "cannot move the copy into place"; fi
        else
          /bin/mv "$S/$N" "$D" || fail "cannot move the copy into place"
        fi
        /bin/rm -rf "$S"
        S=
        """
    }

    /// The whole command: `env -i PATH=… /bin/sh -c '<script>'`.
    public static func command(source: String, _ p: Parameters) throws -> String {
        guard isExpectedPath(source) else { throw BuildError.unexpectedPath }
        guard isVersion(p.version) else { throw BuildError.badVersion }
        return commandPrefix + shellQuote(script(source: source, p))
    }

    /// The AppleScript source the installer runs. Both escaping layers live here: the source path
    /// in shell single quotes inside the script (`command`), then the command as an AppleScript
    /// string literal (`\` and `"`; the requirement's double quotes are escaped at this layer).
    public static func appleScript(source: String, _ p: Parameters) throws -> String {
        "do shell script " + appleScriptLiteral(try command(source: source, p)) + " with administrator privileges"
    }

    /// The same requirement checked in this process on the extracted bundle, before the password
    /// dialog: an early, friendly failure only. The security check is step 5 of the script, on the
    /// root-owned copy.
    public static func satisfies(bundle: URL, requirement: String) -> Bool {
        var code: SecStaticCode?
        var req: SecRequirement?
        guard SecStaticCodeCreateWithPath(bundle as CFURL, [], &code) == errSecSuccess, let code,
              SecRequirementCreateWithString(requirement as CFString, [], &req) == errSecSuccess, let req else { return false }
        let flags = SecCSFlags(rawValue: kSecCSStrictValidate | kSecCSCheckNestedCode | kSecCSCheckAllArchitectures)
        return SecStaticCodeCheckValidity(code, flags, req) == errSecSuccess
    }
}
