#!/usr/bin/env bash
# Build, sign, and notarize the macOS .app. Run this ON A MAC, from the repo
# root: mac-os/scripts/build_and_sign.sh
#
# Requires (see mac-os/CODE_SIGNING.md for how to get each of these):
#   - Xcode command line tools (codesign, xcrun) installed
#   - A "Developer ID Application" certificate in your login keychain
#   - An App Store Connect API key for notarytool (or an Apple ID +
#     app-specific password, see the notarytool section below)
#
# Env vars this script reads:
#   DEVELOPER_ID_APPLICATION   "Developer ID Application: Your Name (TEAMID)"
#                              -- exact string as it appears in
#                              `security find-identity -v -p codesigning`
#   NOTARY_PROFILE             name of a keychain profile created once via
#                              `xcrun notarytool store-credentials`
#                              (see CODE_SIGNING.md) -- avoids putting Apple
#                              credentials in this script or the environment
#                              on every run.
set -euo pipefail

if [[ "$(uname)" != "Darwin" ]]; then
    echo "This script only runs on macOS (needs codesign/notarytool)." >&2
    exit 1
fi

: "${DEVELOPER_ID_APPLICATION:?Set DEVELOPER_ID_APPLICATION to your \"Developer ID Application: ...\" identity}"
: "${NOTARY_PROFILE:?Set NOTARY_PROFILE to the keychain profile name from 'xcrun notarytool store-credentials'}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

APP_NAME="CarmenFocus"
APP_PATH="dist/${APP_NAME}.app"
ZIP_PATH="dist/${APP_NAME}.zip"
ENTITLEMENTS="mac-os/entitlements.plist"

echo "== 1/5: building with PyInstaller =="
rm -rf build dist
pyinstaller mac-os/CarmenFocus.spec

echo "== 2/5: signing every embedded binary (deepest first), then the app =="
# --deep would seem like the shortcut, but Apple's own guidance is to sign
# from the inside out explicitly -- --deep on a bundle this size (Python +
# pyobjc's many .so/.dylib files) has a history of silently skipping nested
# binaries. Signing bottom-up avoids relying on that.
find "$APP_PATH" \( -name "*.so" -o -name "*.dylib" \) -print0 | while IFS= read -r -d '' f; do
    codesign --force --options runtime --timestamp \
        --entitlements "$ENTITLEMENTS" \
        --sign "$DEVELOPER_ID_APPLICATION" "$f"
done
codesign --force --options runtime --timestamp \
    --entitlements "$ENTITLEMENTS" \
    --sign "$DEVELOPER_ID_APPLICATION" "$APP_PATH"

echo "== 3/5: verifying the signature locally =="
codesign --verify --deep --strict --verbose=2 "$APP_PATH"

echo "== 4/5: submitting for notarization (this polls Apple and can take several minutes) =="
ditto -c -k --keepParent "$APP_PATH" "$ZIP_PATH"
xcrun notarytool submit "$ZIP_PATH" --keychain-profile "$NOTARY_PROFILE" --wait

echo "== 5/5: stapling the notarization ticket =="
xcrun stapler staple "$APP_PATH"

echo "== verifying Gatekeeper accepts the final build =="
spctl -a -vv "$APP_PATH"

echo
echo "Done: $APP_PATH is signed, notarized, and stapled."
