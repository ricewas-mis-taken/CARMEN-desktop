# macOS code signing & notarization

Without this, the built `.app` triggers Gatekeeper's "cannot be opened
because it is from an unidentified developer / will damage your computer"
block on any machine it wasn't built on (confirmed on a real Mac, see
`PORT_SPEC.md`'s Subsystem 8 section). This doc is everything needed to go
from a fresh Apple ID to a signed, notarized, double-clickable `.app`.

## What I can't do for you

Enrolling in the Apple Developer Program requires your own Apple ID, a
payment method, and Apple's identity verification -- that's an account/payment
action, not a coding one, so it has to be you. Everything after enrollment
(the actual signing/notarization pipeline) is already built and automated in
`mac-os/scripts/build_and_sign.sh` -- once you've done the one-time setup
below, running the build is one command.

## One-time setup (~20-30 min, one $99/year payment)

1. **Enroll in the Apple Developer Program**: https://developer.apple.com/programs/enroll/
   - $99/year. Personal Apple ID is fine, no company/DUNS number needed for
     an individual account.
   - Apple's verification can take anywhere from a few minutes to ~48 hours.

2. **Create a "Developer ID Application" certificate**, the one that signs
   apps distributed outside the Mac App Store:
   - Open Xcode -> Settings -> Accounts -> add your Apple ID if not already
     there -> select your team -> "Manage Certificates" -> `+` -> "Developer
     ID Application".
   - (No Xcode project needed for this app -- Xcode is just acting as a
     certificate-request UI here.)
   - Confirm it landed in your login keychain: run
     `security find-identity -v -p codesigning` and copy the exact string
     it prints, e.g. `Developer ID Application: Lucas Tang (ABCDE12345)`.
     That string is `DEVELOPER_ID_APPLICATION` below.

3. **Create an app-specific password for notarytool** (notarytool can't use
   your normal Apple ID password):
   - Go to https://account.apple.com/account/manage -> Sign-In and Security
     -> App-Specific Passwords -> generate one, label it "notarytool".
   - Find your Team ID: https://developer.apple.com/account -> Membership
     details, or it's the parenthesized part of the certificate string above.

4. **Store those as a keychain profile** so the build script never needs
   your Apple ID/password directly:
   ```bash
   xcrun notarytool store-credentials "carmen-notary" \
     --apple-id "your-apple-id@example.com" \
     --team-id "ABCDE12345" \
     --password "the-app-specific-password-from-step-3"
   ```
   `carmen-notary` here is the profile name -- it's what `NOTARY_PROFILE`
   below refers to.

## Every build after that

```bash
export DEVELOPER_ID_APPLICATION="Developer ID Application: Lucas Tang (ABCDE12345)"
export NOTARY_PROFILE="carmen-notary"
mac-os/scripts/build_and_sign.sh
```

This builds with PyInstaller (`mac-os/CarmenFocus.spec`, which already has
the `mac_os` package's pathex/hiddenimports baked in -- see that file's
header comment for why a bare `pyinstaller main.py` crashes on launch),
signs every embedded binary and the app bundle with the hardened runtime
(`mac-os/entitlements.plist`), submits it to Apple's notary service and
waits for a result, staples the ticket to the app so it verifies offline,
and finally re-checks it with `spctl` the same way Gatekeeper would on a
fresh machine.

Output: `dist/CarmenFocus.app`, ready to zip and hand to anyone.

## Troubleshooting

- **`errSecInternalComponent` / codesign hangs**: your login keychain is
  locked. `security unlock-keychain` first.
- **notarytool rejects the submission**: run
  `xcrun notarytool log <submission-id> --keychain-profile carmen-notary`
  to get Apple's actual rejection reason (almost always a specific
  unsigned/mis-signed nested binary -- re-run the signing loop, it's
  idempotent).
- **`spctl -a -vv` still says rejected after stapling**: staple only
  succeeds after notarization actually completed; re-run
  `xcrun stapler staple dist/CarmenFocus.app` on its own once the submit
  step reports `status: Accepted`.
