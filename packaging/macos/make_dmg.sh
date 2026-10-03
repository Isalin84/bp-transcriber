#!/usr/bin/env bash
# Упаковка dist/BP Transcriber.app в dist/BP-Transcriber-<version>-macos-arm64.dmg
#
# Содержимое образа: приложение, ярлык «Applications», «Как открыть.txt».
# Если есть фон (packaging/macos/assets/dmg-background.png [+ @2x] или --background PATH), окно
# Finder оформляется через AppleScript (660×400, иконки 128). При любой ошибке оформления
# (нет GUI-сессии в CI, нет доступа к Finder, таймаут) собирается простой образ.
#
# Использование: packaging/macos/make_dmg.sh [--background PATH | --no-background]
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP_NAME="BP Transcriber"
APP="$ROOT/dist/$APP_NAME.app"
README_TXT="$ROOT/packaging/macos/Как открыть.txt"
README_NAME="Как открыть.txt"
VOLNAME="$APP_NAME"

BACKGROUND="$ROOT/packaging/macos/assets/dmg-background.png"
OSASCRIPT_TIMEOUT="${BP_DMG_OSASCRIPT_TIMEOUT:-60}"  # секунд
while [[ $# -gt 0 ]]; do
    case "$1" in
        --background) BACKGROUND="$2"; shift 2 ;;
        --no-background) BACKGROUND=""; shift ;;
        *) echo "Неизвестный аргумент: $1" >&2; exit 2 ;;
    esac
done

VERSION="$(sed -n 's/^__version__ *= *["'\'']\([^"'\'']*\)["'\''].*/\1/p' "$ROOT/bp_transcriber/__init__.py")"
[[ -n "$VERSION" ]] || { echo "Ошибка: не найден __version__" >&2; exit 1; }
DMG="$ROOT/dist/BP-Transcriber-$VERSION-macos-arm64.dmg"

[[ -d "$APP" ]] || { echo "Ошибка: нет $APP — сначала python packaging/build_app.py" >&2; exit 1; }
codesign --verify --deep --strict "$APP" || { echo "Ошибка: подпись $APP не прошла проверку" >&2; exit 1; }

WORK="$ROOT/build/dmg"
STAGING="$WORK/staging"
RW_DMG="$WORK/rw.dmg"
rm -rf "$WORK"
mkdir -p "$STAGING"

echo "Готовлю содержимое образа"
ditto "$APP" "$STAGING/$APP_NAME.app"
ln -s /Applications "$STAGING/Applications"
cp "$README_TXT" "$STAGING/$README_NAME"

# Отсоединить ранее смонтированные тома с тем же именем (иначе Finder перепутает окна).
detach_existing() {
    local dev
    for dev in $(hdiutil info | awk -v vol="/Volumes/$VOLNAME" '
        /^\/dev\/disk/ { d = $1 }
        index($0, vol) { print d }' | sort -u); do
        hdiutil detach "$dev" -force -quiet || true
    done
}

# Запуск команды с жёстким таймаутом (osascript может зависнуть без GUI-сессии).
run_with_timeout() {
    local seconds="$1"; shift
    "$@" &
    local pid=$!
    ( sleep "$seconds"; kill -TERM "$pid" 2>/dev/null; sleep 2; kill -KILL "$pid" 2>/dev/null ) &
    local watchdog=$!
    local code=0
    wait "$pid" || code=$?
    kill "$watchdog" 2>/dev/null || true
    wait "$watchdog" 2>/dev/null || true
    return "$code"
}

# Оформление окна Finder. Возвращает ненулевой код при любой ошибке.
style_volume() {
    local mount="$1"
    local script="$WORK/layout.applescript"
    cat > "$script" <<APPLESCRIPT
on run
    with timeout of 30 seconds
    tell application "Finder"
        tell disk "$VOLNAME"
            open
            delay 1
            set current view of container window to icon view
            set toolbar visible of container window to false
            set statusbar visible of container window to false
            set the bounds of container window to {200, 120, 860, 548}
            set viewOptions to the icon view options of container window
            set arrangement of viewOptions to not arranged
            set icon size of viewOptions to 128
            set text size of viewOptions to 13
            set background picture of viewOptions to file ".background:background.tiff"
            set position of item "$APP_NAME.app" of container window to {165, 200}
            set position of item "Applications" of container window to {495, 200}
            set position of item "$README_NAME" of container window to {330, 345}
            close
            open
            update without registering applications
            delay 2
            close
        end tell
    end tell
    end timeout
end run
APPLESCRIPT
    if ! run_with_timeout "$OSASCRIPT_TIMEOUT" osascript "$script"; then
        echo "osascript не справился (ошибка или таймаут ${OSASCRIPT_TIMEOUT} с)" >&2
        return 1
    fi
    # Finder пишет .DS_Store асинхронно — дождаться файла.
    local i
    for i in $(seq 1 20); do
        [[ -f "$mount/.DS_Store" ]] && return 0
        sleep 0.5
    done
    echo "Finder не записал .DS_Store" >&2
    return 1
}

build_plain() {
    echo "Собираю простой образ (без оформления окна)"
    rm -f "$DMG"
    hdiutil create -volname "$VOLNAME" -srcfolder "$STAGING" -fs HFS+ -format ULMO -ov "$DMG" >/dev/null
}

build_styled() {
    mkdir -p "$STAGING/.background"
    local bg2x="${BACKGROUND%.png}@2x.png"
    if [[ -f "$bg2x" ]]; then
        tiffutil -cathidpicheck "$BACKGROUND" "$bg2x" -out "$STAGING/.background/background.tiff" >/dev/null 2>&1
    else
        sips -s format tiff "$BACKGROUND" --out "$STAGING/.background/background.tiff" >/dev/null
    fi

    local app_mb
    app_mb=$(du -sm "$STAGING" | awk '{print $1}')
    hdiutil create -volname "$VOLNAME" -srcfolder "$STAGING" -fs HFS+ -format UDRW \
        -size "$((app_mb + app_mb / 10 + 50))m" -ov "$RW_DMG" >/dev/null

    detach_existing
    local attach_out dev mount
    attach_out="$(hdiutil attach "$RW_DMG" -readwrite -noverify -noautoopen)"
    dev="$(echo "$attach_out" | awk '/^\/dev\/disk/ { print $1; exit }')"
    mount="$(echo "$attach_out" | awk -F '\t' '/\/Volumes\// { print $NF; exit }')"
    if [[ -z "$dev" || "$mount" != "/Volumes/$VOLNAME" ]]; then
        echo "Не удалось смонтировать образ как /Volumes/$VOLNAME (получено: '$mount')" >&2
        [[ -n "$dev" ]] && hdiutil detach "$dev" -force -quiet || true
        return 1
    fi

    local styled=0
    if style_volume "$mount"; then
        styled=1
    fi
    rm -rf "$mount/.fseventsd" "$mount/.Trashes" 2>/dev/null || true
    chmod -Rf go-w "$mount" 2>/dev/null || true
    sync
    hdiutil detach "$dev" -quiet || { sleep 2; hdiutil detach "$dev" -force -quiet; }

    if [[ $styled -ne 1 ]]; then
        echo "Оформление окна не удалось" >&2
        return 1
    fi
    rm -f "$DMG"
    hdiutil convert "$RW_DMG" -format ULMO -o "$DMG" -ov >/dev/null
    rm -f "$RW_DMG"
}

if [[ -n "$BACKGROUND" && -f "$BACKGROUND" ]]; then
    echo "Фон: $BACKGROUND"
    if ! build_styled; then
        echo "ВНИМАНИЕ: оформление окна не удалось (нужен доступ к Finder через AppleScript) — простой образ" >&2
        rm -rf "$STAGING/.background" "$RW_DMG"
        build_plain
    fi
else
    build_plain
fi

hdiutil verify "$DMG" >/dev/null
rm -rf "$WORK"
size="$(du -h "$DMG" | awk '{print $1}')"
echo "Готово: $DMG ($size)"
