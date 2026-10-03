#!/usr/bin/env bash
# Минимальная статическая LGPL-сборка ffmpeg для BP Transcriber (macOS arm64).
#
# Результат:
#   vendor/ffmpeg/ffmpeg              — исполняемый файл (только системные библиотеки)
#   vendor/ffmpeg/LICENSE.ffmpeg.txt  — LGPL-уведомление, ссылка на исходники, строка configure
#   vendor/ffmpeg/BUILDINFO.txt       — версия, sha256 исходников, строка configure
#
# Исходники кэшируются в vendor/src/. Повторный запуск пересобирает из кэша.
# Использование: packaging/macos/build_ffmpeg.sh [--force]
set -euo pipefail

FFMPEG_VERSION="7.1.3"
FFMPEG_SHA256="f0bf043299db9e3caacb435a712fc541fbb07df613c4b893e8b77e67baf3adbe"
# Отпечаток ключа подписи релизов FFmpeg (https://ffmpeg.org/download.html#releases)
FFMPEG_GPG_FPR="FCF986EA15E6E293A5644F10B4322F04D67658D8"
MACOS_MIN="12.0"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SRC_CACHE="$ROOT/vendor/src"
OUT_DIR="$ROOT/vendor/ffmpeg"
TARBALL="ffmpeg-$FFMPEG_VERSION.tar.xz"
URL="https://ffmpeg.org/releases/$TARBALL"
SRC_DIR="$SRC_CACHE/ffmpeg-$FFMPEG_VERSION"

FORCE=0
[[ "${1:-}" == "--force" ]] && FORCE=1

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
    echo "Ошибка: скрипт рассчитан на macOS arm64 (Apple Silicon)." >&2
    exit 1
fi
command -v clang >/dev/null || { echo "Ошибка: нужен clang (xcode-select --install)." >&2; exit 1; }

if [[ $FORCE -eq 0 && -x "$OUT_DIR/ffmpeg" ]] && grep -q "version: $FFMPEG_VERSION" "$OUT_DIR/BUILDINFO.txt" 2>/dev/null; then
    echo "ffmpeg $FFMPEG_VERSION уже собран: $OUT_DIR/ffmpeg (используйте --force для пересборки)"
    exit 0
fi

mkdir -p "$SRC_CACHE" "$OUT_DIR"

# --- 1. Исходники ------------------------------------------------------------
if [[ ! -f "$SRC_CACHE/$TARBALL" ]]; then
    echo "Скачиваю $URL"
    curl -fL --retry 3 -o "$SRC_CACHE/$TARBALL.part" "$URL"
    mv "$SRC_CACHE/$TARBALL.part" "$SRC_CACHE/$TARBALL"
fi
if ! file "$SRC_CACHE/$TARBALL" | grep -q "XZ compressed"; then
    echo "Ошибка: $TARBALL не является архивом .tar.xz (возможно, скачалась HTML-страница)." >&2
    exit 1
fi
actual_sha="$(shasum -a 256 "$SRC_CACHE/$TARBALL" | awk '{print $1}')"
if [[ "$actual_sha" != "$FFMPEG_SHA256" ]]; then
    echo "Ошибка: sha256 $TARBALL = $actual_sha, ожидалось $FFMPEG_SHA256" >&2
    exit 1
fi
# Подпись GPG проверяется, если gpg доступен (sha256 выше — обязательная проверка).
if command -v gpg >/dev/null; then
    [[ -f "$SRC_CACHE/$TARBALL.asc" ]] || curl -fsL -o "$SRC_CACHE/$TARBALL.asc" "$URL.asc" || true
    [[ -f "$SRC_CACHE/ffmpeg-devel.asc" ]] || curl -fsL -o "$SRC_CACHE/ffmpeg-devel.asc" https://ffmpeg.org/ffmpeg-devel.asc || true
    if [[ -f "$SRC_CACHE/$TARBALL.asc" && -f "$SRC_CACHE/ffmpeg-devel.asc" ]]; then
        gnupg_home="$(mktemp -d)"
        gpg --homedir "$gnupg_home" --quiet --import "$SRC_CACHE/ffmpeg-devel.asc" 2>/dev/null || true
        if gpg --homedir "$gnupg_home" --status-fd 1 --verify "$SRC_CACHE/$TARBALL.asc" "$SRC_CACHE/$TARBALL" 2>/dev/null \
            | grep -q "VALIDSIG $FFMPEG_GPG_FPR"; then
            echo "Подпись GPG исходников ffmpeg верна ($FFMPEG_GPG_FPR)"
        else
            echo "Ошибка: подпись GPG $TARBALL не прошла проверку." >&2
            rm -rf "$gnupg_home"
            exit 1
        fi
        rm -rf "$gnupg_home"
    fi
fi

rm -rf "$SRC_DIR"
tar -xf "$SRC_CACHE/$TARBALL" -C "$SRC_CACHE"

# --- 2. Конфигурация ---------------------------------------------------------
# Только LGPL, без автодетекта внешних библиотек и системных фреймворков
# (AudioToolbox/VideoToolbox/zlib/iconv и т.п.) — бинарник зависит лишь от libSystem.
# Видеодекодеров нет намеренно: приложение всегда вызывает ffmpeg с -vn.
DEMUXERS="mov,matroska,avi,mp3,ogg,flac,wav,aac,mpegts,asf,amr,amrnb,amrwb,caf,w64,aiff,flv,mpegps,ac3,eac3,pcm_s16le"
DECODERS="aac,aac_latm,mp3,mp3float,mp2,mp2float,opus,vorbis,flac,alac,ac3,eac3,dca,wmav1,wmav2,wmapro,amrnb,amrwb,gsm_ms,adpcm_ima_wav,adpcm_ms,adpcm_ima_qt,pcm_s16le,pcm_s24le,pcm_s32le,pcm_f32le,pcm_f64le,pcm_u8,pcm_s8,pcm_s16be,pcm_s24be,pcm_s32be,pcm_f32be,pcm_mulaw,pcm_alaw"
PARSERS="aac,aac_latm,mpegaudio,opus,vorbis,flac,ac3,dca,gsm,h264,hevc,vp8,vp9,av1,mpeg4video"
FILTERS="aresample,aformat,anull,atrim"
ENCODERS="pcm_s16le,aac"
MUXERS="pcm_s16le,mp4,ipod,wav"

CONFIGURE_ARGS=(
    --prefix="$SRC_DIR/_install"
    --cc=clang
    --arch=arm64 --target-os=darwin
    --extra-cflags="-mmacosx-version-min=$MACOS_MIN"
    --extra-ldflags="-mmacosx-version-min=$MACOS_MIN"
    --pkg-config=false
    --enable-static --disable-shared
    --disable-gpl --disable-nonfree --disable-version3
    --disable-autodetect
    --disable-everything
    --disable-doc --disable-htmlpages --disable-manpages --disable-podpages --disable-txtpages
    --disable-network
    --disable-debug
    --disable-avdevice
    --disable-ffplay --disable-ffprobe --enable-ffmpeg
    --enable-protocol=file,pipe
    --enable-demuxer="$DEMUXERS"
    --enable-decoder="$DECODERS"
    --enable-parser="$PARSERS"
    --enable-filter="$FILTERS"
    --enable-encoder="$ENCODERS"
    --enable-muxer="$MUXERS"
    --enable-bsf=aac_adtstoasc
)

cd "$SRC_DIR"
echo "./configure ${CONFIGURE_ARGS[*]}"
./configure "${CONFIGURE_ARGS[@]}"

# --- 3. Сборка ---------------------------------------------------------------
make -j"$(sysctl -n hw.ncpu)" ffmpeg

cp ffmpeg "$OUT_DIR/ffmpeg.tmp"
strip -x "$OUT_DIR/ffmpeg.tmp"
mv "$OUT_DIR/ffmpeg.tmp" "$OUT_DIR/ffmpeg"
chmod 755 "$OUT_DIR/ffmpeg"

# --- 4. Проверки ---------------------------------------------------------------
if ! "$OUT_DIR/ffmpeg" -hide_banner -L | grep -q "GNU Lesser General Public"; then
    echo "Ошибка: собранный ffmpeg не под LGPL:" >&2
    "$OUT_DIR/ffmpeg" -hide_banner -L >&2
    exit 1
fi
non_system="$(otool -L "$OUT_DIR/ffmpeg" | tail -n +2 | awk '{print $1}' | grep -v -E '^/usr/lib/|^/System/Library/' || true)"
if [[ -n "$non_system" ]]; then
    echo "Ошибка: ffmpeg зависит от несистемных библиотек:" >&2
    echo "$non_system" >&2
    exit 1
fi

# --- 5. Лицензия и сведения о сборке ------------------------------------------
CONFIGURE_LINE="./configure ${CONFIGURE_ARGS[*]/#--prefix=*/--prefix=<build>/_install}"
BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

{
    echo "version: $FFMPEG_VERSION"
    echo "source: $URL"
    echo "source_sha256: $FFMPEG_SHA256"
    echo "built: $BUILD_DATE"
    echo "host: $(sw_vers -productName) $(sw_vers -productVersion) $(uname -m)"
    echo "compiler: $(clang --version | head -n 1)"
    echo "macos_min: $MACOS_MIN"
    echo "license: LGPL-2.1-or-later"
    echo "configure: $CONFIGURE_LINE"
    echo
    echo "ffmpeg -version:"
    "$OUT_DIR/ffmpeg" -hide_banner -version
} > "$OUT_DIR/BUILDINFO.txt"

{
    cat <<EOF
FFmpeg $FFMPEG_VERSION
=====================

BP Transcriber включает исполняемый файл ffmpeg из проекта FFmpeg (https://ffmpeg.org/).
This application bundles the ffmpeg executable from the FFmpeg project.

FFmpeg is licensed under the GNU Lesser General Public License (LGPL) version 2.1
or later. This build was configured with --disable-gpl --disable-nonfree, so no
GPL or non-free components are included. The full text of the LGPL v2.1 follows
below.

ffmpeg is shipped as a separate, unmodified executable that BP Transcriber runs
as a subprocess. You may replace it with your own build of FFmpeg: in the macOS
app bundle it is located at
  BP Transcriber.app/Contents/Frameworks/bin/ffmpeg
(on Windows: <install dir>\\_internal\\bin\\ffmpeg.exe).

Corresponding source code
-------------------------
Source tarball:  $URL
SHA-256:         $FFMPEG_SHA256
No source modifications were made.

Build configuration
-------------------
$CONFIGURE_LINE

Build script: packaging/macos/build_ffmpeg.sh in the BP Transcriber repository
(https://github.com/Isalin84/bp-transcriber).

FFmpeg is a trademark of Fabrice Bellard, originator of the FFmpeg project.

------------------------------------------------------------------------------
EOF
    cat "$SRC_DIR/COPYING.LGPLv2.1"
} > "$OUT_DIR/LICENSE.ffmpeg.txt"

size="$(du -h "$OUT_DIR/ffmpeg" | awk '{print $1}')"
echo
echo "Готово: $OUT_DIR/ffmpeg ($size)"
otool -L "$OUT_DIR/ffmpeg"
