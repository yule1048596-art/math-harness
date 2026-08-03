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

# 「应用程序」符号链接。缺了它，打开 DMG 只看到一个孤零零的 App 图标，用户无从知道
# 要把它拖到哪里去——这是安装体验里最关键的一环。
ln -s /Applications "$STAGING_ROOT/Applications"

VOLUME_ICON="$APP_BUNDLE/Contents/Resources/AppIcon.icns"

rm -f "$DMG_PATH"

# 先做可读写映像并挂载，摆好窗口布局，再压成只读发布映像。
RW_DMG="$STAGING_ROOT/../math-harness-rw-$$.dmg"
trap 'rm -rf "$STAGING_ROOT" "$RW_DMG"' EXIT
hdiutil create \
    -volname "Math Harness" \
    -srcfolder "$STAGING_ROOT" \
    -format UDRW \
    -fs HFS+ \
    -ov \
    "$RW_DMG"

MOUNT_POINT="$(mktemp -d /tmp/math-harness-mount.XXXXXX)"
hdiutil attach "$RW_DMG" -mountpoint "$MOUNT_POINT" -nobrowse -noverify -noautoopen

# 窗口布局优先使用仓库里固化的 .DS_Store。
#
# 布局本身只能靠 Finder 的 AppleScript 接口生成，而它需要自动化授权，在无头 CI 上
# 基本用不了——如果只靠现场生成，发布版反而是唯一没有布局的那个。所以在本地生成一次
# 并提交结果，CI 直接复用。
#
# 重新生成布局：删掉 Resources/dmg/DS_Store 后在本机跑一次本脚本，再把挂载卷根目录的
# .DS_Store 拷回去。
LAYOUT_TEMPLATE="$PROJECT_ROOT/macos/MathHarnessApp/Resources/dmg/DS_Store"
if [[ -f "$LAYOUT_TEMPLATE" ]]; then
    cp "$LAYOUT_TEMPLATE" "$MOUNT_POINT/.DS_Store"
elif ! osascript - "$MOUNT_POINT" <<'APPLESCRIPT'
on run argv
    set mountPath to item 1 of argv
    tell application "Finder"
        set diskName to name of (POSIX file mountPath as alias)
        tell disk diskName
            open
            set current view of container window to icon view
            set toolbar visible of container window to false
            set statusbar visible of container window to false
            set the bounds of container window to {200, 160, 800, 560}
            set viewOptions to the icon view options of container window
            set arrangement of viewOptions to not arranged
            set icon size of viewOptions to 128
            set text size of viewOptions to 13
            set position of item "Math Harness.app" of container window to {150, 190}
            set position of item "Applications" of container window to {450, 190}
            close
            open
            update without registering applications
            delay 2
            close
        end tell
    end tell
end run
APPLESCRIPT
then
    print -u2 "Finder layout step failed; shipping the DMG with default window options."
fi

# 卷图标必须放在 Finder 摆完布局之后再写：先写的话 Finder 在重扫卷内容时会把它清掉，
# 最终映像里就只剩通用磁盘图标。
if [[ -f "$VOLUME_ICON" ]]; then
    cp "$VOLUME_ICON" "$MOUNT_POINT/.VolumeIcon.icns"
    SetFile -a C "$MOUNT_POINT" 2>/dev/null || true
fi

sync
hdiutil detach "$MOUNT_POINT" -force
rmdir "$MOUNT_POINT" 2>/dev/null || true

hdiutil convert "$RW_DMG" -format UDZO -imagekey zlib-level=9 -ov -o "$DMG_PATH"

# 发布前自检。上一版的 DMG 只有一个孤立的 App 图标就发出去了，正是因为打包脚本
# 从不检查自己的产物。安装体验相关的缺失一律让构建失败，装饰性缺失只告警。
VERIFY_MOUNT="$(mktemp -d /tmp/math-harness-verify.XXXXXX)"
hdiutil attach "$DMG_PATH" -mountpoint "$VERIFY_MOUNT" -nobrowse -noverify -noautoopen \
    >/dev/null
VERIFY_FAILED=false
if [[ ! -L "$VERIFY_MOUNT/Applications" ]]; then
    print -u2 "FAIL: DMG is missing the /Applications drop target."
    VERIFY_FAILED=true
fi
if [[ ! -d "$VERIFY_MOUNT/Math Harness.app" ]]; then
    print -u2 "FAIL: DMG is missing Math Harness.app."
    VERIFY_FAILED=true
fi
if [[ ! -f "$VERIFY_MOUNT/.DS_Store" ]]; then
    print -u2 "WARN: no saved window layout; the DMG will open with Finder defaults."
fi
if [[ ! -f "$VERIFY_MOUNT/.VolumeIcon.icns" ]]; then
    print -u2 "WARN: no volume icon; the mounted disk will use the generic icon."
fi
hdiutil detach "$VERIFY_MOUNT" -force >/dev/null
rmdir "$VERIFY_MOUNT" 2>/dev/null || true
if [[ "$VERIFY_FAILED" == "true" ]]; then
    exit 1
fi

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
