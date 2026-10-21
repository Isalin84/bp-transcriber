# Сборка BP Transcriber

PyInstaller (onedir) поверх пакетов `bp_transcriber` + `gigaam_transcriber`, встроенный LGPL-ffmpeg.

| Платформа | Результат |
|---|---|
| macOS (Apple Silicon) | `dist/BP Transcriber.app`, `dist/BP-Transcriber-<version>-macos-arm64.dmg` |
| Windows | `dist/BP Transcriber/BP Transcriber.exe` (onedir) |

Версия берётся из `bp_transcriber/__init__.py` (`__version__`).

## Требования (macOS)

- Mac на Apple Silicon, Xcode Command Line Tools (`xcode-select --install`): clang, codesign, hdiutil.
- Виртуальное окружение со всеми зависимостями (`pip install -e ".[diarization,app,build]"`
  и GigaAM: `requirements/gigaam.txt`). PyInstaller ≥ 6.11.
- Для проверки форматов — Homebrew ffmpeg (только для генерации тестовых файлов, в сборку не попадает).

## Команды

```bash
# 1. ffmpeg: минимальная статическая LGPL-сборка → vendor/ffmpeg/{ffmpeg,LICENSE.ffmpeg.txt,BUILDINFO.txt}
packaging/macos/build_ffmpeg.sh            # исходники кэшируются в vendor/src/, --force пересобирает

# 2. приложение → dist/BP Transcriber.app (ad-hoc подпись + codesign --verify --deep --strict)
.venv/bin/python packaging/build_app.py --clean [--selftest]
#    --allow-missing-ffmpeg — собрать без встроенного ffmpeg (будет искаться в PATH)

# 3. образ → dist/BP-Transcriber-<version>-macos-arm64.dmg
packaging/macos/make_dmg.sh                # --no-background — без оформления окна
```

`build_app.py` сам добавляет корень репозитория в `PYTHONPATH`, поэтому в сборку попадают пакеты
этой рабочей копии, даже если в окружении есть editable-установка из другого каталога.
Весь пакет `gigaam_transcriber` собирается целиком (`collect_submodules`) — новые модули
попадают в сборку без правки spec.

## Проверка

```bash
APP="dist/BP Transcriber.app/Contents/MacOS/BP Transcriber"
"$APP" --selftest        # JSON в терминал, код 0 = все обязательные проверки прошли
"$APP" --selftest-ui     # скрытое окно pywebview, вызов JS API get_state, код 0
codesign --verify --deep --strict --verbose=2 "dist/BP Transcriber.app"
```

`--selftest` работает без сети и токена: импорты, встроенный ffmpeg (из бандла, не из PATH),
декодирование и AAC-превью сгенерированного WAV, Silero VAD, torch CPU/MPS, импорт pyannote
(`SpeakerDiarization`), speechbrain (`EncoderClassifier`), sklearn, бэкенд keyring, что кэши
(HF, torch hub, GigaAM, каталоги приложения) лежат в домашнем каталоге, и GigaAM на CPU/MPS,
если модель уже скачана. Журналы: `~/Library/Logs/BP Transcriber/`.

## Что внутри spec (`packaging/bp_transcriber.spec`)

- Точка входа `packaging/launcher.py` (у `__main__.py` относительные импорты). Во frozen-режиме
  launcher ставит `MPLBACKEND=Agg` и, если cwd не доступен для записи (запуск из Finder, cwd=`/`),
  переходит в каталог кэша приложения.
- ffmpeg → `Contents/Frameworks/bin/ffmpeg` (`sys._MEIPASS/bin`, см. `audio_io.find_ffmpeg`).
- UI → `bp_transcriber/ui` (как ожидает `paths.ui_dir()`), без `ui/mock/`.
- `module_collection_mode="pyz+py"` для gigaam, pyannote, speechbrain, lightning*, torchmetrics,
  silero_vad и др.: torch.jit/inspect читают исходники, speechbrain строит lazy-импорты по списку
  файлов пакета.
- Исключены: tkinter, onnx/onnxruntime, torchcodec (pyannote получает волну из памяти), triton,
  tensorboard, transformers, IPython/jupyter, pytest, grpc, sqlalchemy/alembic, numba, pyarrow.
  matplotlib и pandas **нужны** (их импортируют torchmetrics и pyannote.database).
- UPX и strip не используются (на macOS ломают подписи).

## Известные ограничения

- **Приложение не подписано Developer ID и не нотариально заверено.** Gatekeeper блокирует первый
  запуск — инструкция для пользователя в `packaging/macos/Как открыть.txt` (лежит в DMG).
- Оформление окна DMG делает Finder через AppleScript. Без GUI-сессии (по SSH) или без разрешения
  на управление Finder скрипт собирает простой образ (osascript ограничен таймаутом,
  `BP_DMG_OSASCRIPT_TIMEOUT`, по умолчанию 60 с).
- Перетаскивание файлов на иконку в Dock не поддерживается (нет обработки Apple Events open-document).

## Подпись Developer ID и нотаризация (позже)

1. Сертификат «Developer ID Application» в связке ключей; entitlements для hardened runtime
   (PyTorch/JIT): `com.apple.security.cs.allow-jit`, `com.apple.security.cs.allow-unsigned-executable-memory`,
   `com.apple.security.cs.disable-library-validation`.
2. Сборка с подписью:
   ```bash
   export BP_CODESIGN_IDENTITY="Developer ID Application: <Имя> (<TEAMID>)"
   export BP_ENTITLEMENTS=packaging/macos/entitlements.plist
   .venv/bin/python packaging/build_app.py --clean
   ```
   PyInstaller подписывает все бинарники; при сбое проверки `build_app.py` переподписывает
   `codesign --force --deep --options runtime --timestamp -s "$BP_CODESIGN_IDENTITY"`.
   Встроенный `ffmpeg` тоже должен быть подписан с `--options runtime`.
3. DMG: `packaging/macos/make_dmg.sh`, затем
   `codesign -s "$BP_CODESIGN_IDENTITY" --timestamp dist/BP-Transcriber-*.dmg`.
4. Нотаризация:
   ```bash
   xcrun notarytool store-credentials bp-notary --apple-id <id> --team-id <TEAMID>
   xcrun notarytool submit dist/BP-Transcriber-*.dmg --keychain-profile bp-notary --wait
   xcrun stapler staple dist/BP-Transcriber-*.dmg
   spctl -a -t open --context context:primary-signature -v dist/BP-Transcriber-*.dmg
   ```

## Windows (на Windows-машине, PowerShell)

Раньше это делал GitHub Actions; теперь вручную. Нужны Python 3.12, Inno Setup 6, полный ffmpeg в PATH
(только для проверки форматов).

```powershell
pip install -r requirements/lock-win-x64.txt --extra-index-url https://download.pytorch.org/whl/cpu
pip install --no-deps -r requirements/gigaam.txt; pip install --no-deps -e .
./packaging/windows/fetch_ffmpeg.ps1                    # LGPL ffmpeg -> vendor/ffmpeg/ffmpeg.exe
python scripts/check_formats.py
python packaging/build_app.py --clean
& "dist\BP Transcriber\BP Transcriber.exe" --selftest   # отчёт также в %BP_HOME%\logs
# WebView2 bootstrapper (обязателен для /DRequireWebView2; проверить подпись Microsoft)
Invoke-WebRequest https://go.microsoft.com/fwlink/p/?LinkId=2124703 -OutFile packaging/windows/redist/MicrosoftEdgeWebview2Setup.exe
$defs = @(python packaging/windows/prepare_wizard_images.py)
& "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" "/DAppVersion=<версия>" "/DRequireWebView2" @defs packaging\windows\bp_transcriber.iss
# -> dist\BP-Transcriber-<версия>-windows-x64-setup.exe
```

## Релиз (вручную)

1. `bash scripts/check.sh`, версия в `bp_transcriber/__init__.py`, тег `v<версия>`.
2. Установщики собираются на своих платформах (DMG на Mac, setup.exe на Windows) и проверяются selftest.
3. `shasum -a 256 BP-Transcriber-* > SHA256SUMS.txt`
4. Текст релиза: `packaging/release-notes-template.md` (подставить `{{VERSION}}` и `{{TAG}}`), затем
   `gh release create v<версия> --draft --notes-file release-notes.md BP-Transcriber-* SHA256SUMS.txt`.
   Публикует черновик человек после проверки на реальных машинах.
