#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
PROJECT_ROOT="${SCRIPT_DIR:h}"
PACKAGE_ROOT="$PROJECT_ROOT/macos/MathHarnessApp"
BUILD_ROOT="$PROJECT_ROOT/.build/macos-package"
BACKEND_WORK="$BUILD_ROOT/backend-work"
BACKEND_DIST="$BUILD_ROOT/backend-dist"
DIST_ROOT="$PROJECT_ROOT/dist"
VERSION="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' \
    "$PACKAGE_ROOT/Resources/Info.plist")"
ARCHITECTURE="$(uname -m)"
ARCHIVE_PATH="$DIST_ROOT/Math-Harness-$VERSION-macOS-$ARCHITECTURE.zip"
STAGING_ROOT="$(mktemp -d /tmp/math-harness-app.XXXXXX)"
APP_BUNDLE="$STAGING_ROOT/Math Harness.app"
APP_CONTENTS="$APP_BUNDLE/Contents"
SIGN_IDENTITY="${MATH_HARNESS_SIGN_IDENTITY:--}"

if [[ "$STAGING_ROOT" != /tmp/math-harness-app.* ]]; then
    print -u2 "Refusing to use an unexpected staging path: $STAGING_ROOT"
    exit 1
fi
trap 'rm -rf "$STAGING_ROOT"' EXIT

rm -rf "$BUILD_ROOT"
rm -f "$ARCHIVE_PATH"
mkdir -p "$BACKEND_WORK" "$BACKEND_DIST" \
    "$APP_CONTENTS/MacOS" "$APP_CONTENTS/Resources/backend" "$DIST_ROOT"

print "Building standalone Python backend…"
PYINSTALLER_ARGS=(
    --noconfirm
    --clean
    --onedir
    --name math-harness-server
    --paths "$PROJECT_ROOT/src"
    --distpath "$BACKEND_DIST"
    --workpath "$BACKEND_WORK/work"
    --specpath "$BACKEND_WORK/spec"
)
if [[ "$SIGN_IDENTITY" != "-" ]]; then
    PYINSTALLER_ARGS+=(--codesign-identity "$SIGN_IDENTITY")
fi

uv run --frozen --no-editable --extra llm \
    --with 'pyinstaller>=6.14,<7' \
    pyinstaller "${PYINSTALLER_ARGS[@]}" \
    "$PROJECT_ROOT/src/math_harness/server.py"

print "Building native SwiftUI app…"
swift build \
    --package-path "$PACKAGE_ROOT" \
    --configuration release \
    --product MathHarnessApp
SWIFT_BIN_DIR="$(swift build \
    --package-path "$PACKAGE_ROOT" \
    --configuration release \
    --show-bin-path)"

ditto "$SWIFT_BIN_DIR/MathHarnessApp" "$APP_CONTENTS/MacOS/MathHarnessApp"
ditto "$BACKEND_DIST/math-harness-server" "$APP_CONTENTS/Resources/backend"
ditto "$PACKAGE_ROOT/Resources/Info.plist" "$APP_CONTENTS/Info.plist"
chmod 755 "$APP_CONTENTS/MacOS/MathHarnessApp" \
    "$APP_CONTENTS/Resources/backend/math-harness-server"
xattr -cr "$APP_BUNDLE"

if [[ "$SIGN_IDENTITY" == "-" ]]; then
    print "Applying local ad-hoc signature…"
    codesign --force --sign - "$APP_CONTENTS/MacOS/MathHarnessApp"
    codesign --force --sign - "$APP_BUNDLE"
else
    print "Signing with Developer ID identity…"
    codesign \
        --force \
        --options runtime \
        --timestamp \
        --sign "$SIGN_IDENTITY" \
        "$APP_CONTENTS/MacOS/MathHarnessApp"
    codesign \
        --force \
        --options runtime \
        --timestamp \
        --sign "$SIGN_IDENTITY" \
        "$APP_BUNDLE"
fi

codesign --verify --deep --strict --verbose=2 "$APP_BUNDLE"

print "Archiving verified App…"
ditto -c -k --norsrc --keepParent "$APP_BUNDLE" "$ARCHIVE_PATH"
VERIFY_ROOT="$STAGING_ROOT/verify"
mkdir -p "$VERIFY_ROOT"
ditto -x -k "$ARCHIVE_PATH" "$VERIFY_ROOT"
codesign --verify --deep --strict --verbose=2 "$VERIFY_ROOT/Math Harness.app"

print "Built: $ARCHIVE_PATH"
