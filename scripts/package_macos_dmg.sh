#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h}"
VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' \
    "$PROJECT_ROOT/macos/MathHarnessApp/Resources/Info.plist")"
ARCHITECTURE="$(uname -m)"
ARCHIVE_PATH="$PROJECT_ROOT/dist/Math-Harness-$VERSION-macOS-$ARCHITECTURE.zip"
DMG_PATH="$PROJECT_ROOT/dist/Math-Harness-$VERSION-macOS-$ARCHITECTURE.dmg"
NOTARY_PROFILE="${MATH_HARNESS_NOTARY_PROFILE:-}"
SKIP_APP_BUILD="${MATH_HARNESS_SKIP_APP_BUILD:-false}"
STAGING_ROOT="$(mktemp -d /tmp/math-harness-dmg.XXXXXX)"

if [[ "$STAGING_ROOT" != /tmp/math-harness-dmg.* ]]; then
    print -u2 "Refusing to use an unexpected staging path: $STAGING_ROOT"
    exit 1
fi
trap 'rm -rf "$STAGING_ROOT"' EXIT

if [[ "$SKIP_APP_BUILD" != "true" ]]; then
    "$SCRIPT_DIR/build_macos_app.sh"
fi
if [[ ! -f "$ARCHIVE_PATH" ]]; then
    print -u2 "Missing app archive: $ARCHIVE_PATH"
    exit 1
fi
ditto -x -k "$ARCHIVE_PATH" "$STAGING_ROOT"
APP_BUNDLE="$STAGING_ROOT/Math Harness.app"
codesign --verify --deep --strict --verbose=2 "$APP_BUNDLE"

rm -f "$DMG_PATH"
hdiutil create \
    -volname "Math Harness" \
    -srcfolder "$STAGING_ROOT" \
    -format UDZO \
    -ov \
    "$DMG_PATH"

if [[ -n "$NOTARY_PROFILE" ]]; then
    if ! xcrun notarytool help >/dev/null 2>&1; then
        print -u2 "notarytool is unavailable. Install full Xcode before notarizing."
        exit 1
    fi
    xcrun notarytool submit "$DMG_PATH" \
        --keychain-profile "$NOTARY_PROFILE" \
        --wait
    xcrun stapler staple "$DMG_PATH"
fi

print "Packaged: $DMG_PATH"
