# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec для BP Transcriber (onedir; на macOS ещё и .app через BUNDLE).

Запуск — через ``python packaging/build_app.py`` (проверяет ffmpeg, чистит, подписывает).
Напрямую: ``pyinstaller --noconfirm packaging/bp_transcriber.spec`` из корня репозитория.
"""

import os
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

ROOT = Path(SPECPATH).resolve().parent  # noqa: F821 — SPECPATH задаёт PyInstaller
PACKAGING = ROOT / "packaging"
# Пакеты рабочей копии важнее установленных (в т.ч. editable-установки из другого каталога).
sys.path.insert(0, str(ROOT))

APP_NAME = "BP Transcriber"
BUNDLE_ID = "ru.bestpractice.transcriber"


def _read_version() -> str:
    text = (ROOT / "bp_transcriber" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.M)
    if not match:
        raise SystemExit("Не найден __version__ в bp_transcriber/__init__.py")
    return match.group(1)


VERSION = os.environ.get("BP_VERSION") or _read_version()


def _editable_roots(*packages: str) -> list[str]:
    """Каталоги пакетов из editable-установок (setuptools-finder), которых модульный граф PyInstaller не видит."""
    import importlib.util

    roots = []
    for package in packages:
        try:
            spec = importlib.util.find_spec(package)
        except (ImportError, ValueError):
            spec = None
        if spec is None or not spec.submodule_search_locations:
            continue
        parent = str(Path(list(spec.submodule_search_locations)[0]).resolve().parent)
        if parent not in roots and parent != str(ROOT):
            roots.append(parent)
    return roots


# gigaam ставится editable из GigaAM/ (см. requirements/gigaam.txt).
EXTRA_PATHS = _editable_roots("gigaam")
for _path in EXTRA_PATHS:
    if _path not in sys.path:
        sys.path.append(_path)


def _installed(dist: str) -> bool:
    try:
        from importlib.metadata import distribution

        distribution(dist)
        return True
    except Exception:  # noqa: BLE001
        return False


def _importable(module: str) -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


# --- Данные -------------------------------------------------------------------
datas = [
    # Совпадает с paths.ui_dir(): <_MEIPASS>/bp_transcriber/ui. mock/ — только для разработки в браузере.
    (str(ROOT / "bp_transcriber" / "ui"), "bp_transcriber/ui"),
]

DATA_PACKAGES = {
    "pyannote.audio": {},
    "pyannote.pipeline": {},
    "pyannote.database": {},
    "pyannote.core": {},
    "lightning": {"excludes": ["app/**", "store/**"]},
    "lightning_fabric": {},
    "pytorch_lightning": {},
    "silero_vad": {"excludes": ["**/*.onnx"]},  # нужен только JIT (+safetensors)
    "speechbrain": {},  # version.txt, yaml-конфиги; lazy-импорт сканирует каталог пакета
    "webview": {"excludes": ["lib/**"]} if not IS_WIN else {},
    "asteroid_filterbanks": {},
    "torch_audiomentations": {},
    "torchmetrics": {},
    "sklearn": {},
    "hydra": {},
    "omegaconf": {},
    "gigaam": {},
}
for _pkg, _kw in DATA_PACKAGES.items():
    if _importable(_pkg):
        datas += collect_data_files(_pkg, **_kw)

METADATA = [
    "torch", "torchaudio", "pyannote.audio", "pyannote.core", "pyannote.database",
    "pyannote.metrics", "pyannote.pipeline", "lightning", "pytorch_lightning", "lightning_fabric",
    "lightning-utilities", "torchmetrics", "huggingface_hub", "safetensors", "speechbrain", "tqdm",
    "numpy", "packaging", "filelock", "requests", "regex", "scikit-learn", "scipy", "silero-vad",
    "hydra-core", "omegaconf", "sentencepiece", "soundfile", "keyring", "pywebview", "bottle",
    "python-docx", "platformdirs", "opentelemetry-api", "opentelemetry-sdk", "asteroid-filterbanks",
    "torch-audiomentations", "pytorch-metric-learning", "einops", "julius", "optuna", "pandas",
    "matplotlib", "rich", "pyyaml", "hyperpyyaml", "sympy", "networkx", "fsspec", "jinja2",
    "gigaam", "importlib_metadata", "typing_extensions",
]
for _dist in METADATA:
    if _installed(_dist):
        datas += copy_metadata(_dist)
# Метаданные нужны и ради entry points: keyring ищет бэкенды, opentelemetry — провайдеры контекста.

# --- Бинарники -------------------------------------------------------------------
FFMPEG_NAME = "ffmpeg.exe" if IS_WIN else "ffmpeg"
FFMPEG = ROOT / "vendor" / "ffmpeg" / FFMPEG_NAME
binaries = []
if FFMPEG.is_file():
    binaries.append((str(FFMPEG), "bin"))
    for _extra in ("LICENSE.ffmpeg.txt", "BUILDINFO.txt"):
        if (FFMPEG.parent / _extra).is_file():
            datas.append((str(FFMPEG.parent / _extra), "bin"))
elif os.environ.get("BP_ALLOW_MISSING_FFMPEG") != "1":
    raise SystemExit(f"Нет {FFMPEG}: соберите ffmpeg (packaging/macos/build_ffmpeg.sh) "
                     "или задайте BP_ALLOW_MISSING_FFMPEG=1")

for _license in ("LICENSE", "NOTICE.md"):
    if (ROOT / _license).is_file():
        datas.append((str(ROOT / _license), "."))

# --- Скрытые импорты -------------------------------------------------------------


def _safe_submodules(package: str, skip: tuple[str, ...] = ()) -> list[str]:
    if not _importable(package):
        return []
    mods = collect_submodules(package, on_error="ignore")
    return [m for m in mods if not any(m == s or m.startswith(s + ".") for s in skip)]


hiddenimports = []
hiddenimports += _safe_submodules("bp_transcriber")
hiddenimports += _safe_submodules("gigaam_transcriber")  # весь пакет: новые модули попадают автоматически
hiddenimports += _safe_submodules("gigaam", skip=("gigaam.onnx_utils",))
for _pkg in ("omegaconf", "hydra", "antlr4"):
    hiddenimports += _safe_submodules(_pkg, skip=("hydra.test_utils", "hydra._internal.instantiate.tests"))
for _pkg in ("pyannote.audio", "pyannote.core", "pyannote.pipeline", "pyannote.database", "pyannote.metrics"):
    hiddenimports += _safe_submodules(_pkg, skip=("pyannote.audio.cli", "pyannote.metrics.cli",
                                                  "pyannote.metrics.plot", "pyannote.core.notebook"))
# speechbrain целиком: lazy_export_all + DeprecatedModuleRedirect импортируют модули по имени.
hiddenimports += _safe_submodules("speechbrain", skip=(
    "speechbrain.integrations.k2_fsa", "speechbrain.integrations.numba", "speechbrain.integrations.nlp",
    "speechbrain.integrations.huggingface", "speechbrain.k2_integration", "speechbrain.lm.ngram",
))
for _pkg in ("lightning_fabric", "pytorch_lightning", "torchmetrics", "asteroid_filterbanks",
             "torch_audiomentations", "torch_pitch_shift", "pytorch_metric_learning", "julius",
             "silero_vad", "hyperpyyaml"):
    hiddenimports += _safe_submodules(_pkg)
hiddenimports += _safe_submodules("lightning", skip=("lightning.app", "lightning.store", "lightning.data"))
hiddenimports += [
    "sklearn.cluster", "sklearn.cluster._agglomerative", "sklearn.metrics.pairwise",
    "sklearn.utils._typedefs", "sklearn.neighbors._partition_nodes",
    "scipy.cluster.hierarchy", "scipy.spatial.distance", "scipy.special.cython_special",
    "soundfile", "sentencepiece", "docx", "bottle", "platformdirs",
    "keyring.backends.null", "keyring.backends.fail", "keyring.backends.chainer",
]
if IS_MAC:
    hiddenimports += ["keyring.backends.macOS", "webview.platforms.cocoa"]
    hiddenimports += _safe_submodules("keyring.backends.macOS")
if IS_WIN:
    hiddenimports += ["keyring.backends.Windows", "webview.platforms.edgechromium", "webview.platforms.winforms",
                      "clr", "clr_loader"]
hiddenimports = sorted(set(hiddenimports))

# --- Исключения -------------------------------------------------------------------
excludes = [
    "tkinter", "_tkinter", "turtle", "idlelib",
    "onnx", "onnxruntime", "torchcodec", "triton", "tensorboard", "tensorflow", "tensorboardX",
    "IPython", "ipykernel", "ipywidgets", "jupyter", "jupyter_client", "jupyter_core", "notebook",
    "nbformat", "jedi", "pytest", "_pytest", "transformers", "torchvision", "torch.utils.tensorboard",
    "numba", "llvmlite", "pyarrow", "grpc", "opentelemetry.exporter.otlp.proto.grpc",
    "sqlalchemy", "alembic", "pyannoteai", "PyQt5", "PyQt6", "PySide2", "PySide6", "qtpy", "gi",
    "matplotlib.backends.backend_tkagg", "matplotlib.backends.backend_qtagg", "matplotlib.backends.backend_qt5agg",
    "gigaam.onnx_utils",
]
if IS_MAC:
    excludes += ["webview.platforms.qt", "webview.platforms.gtk", "webview.platforms.winforms",
                 "webview.platforms.edgechromium", "webview.platforms.mshtml", "webview.platforms.cef",
                 "webview.platforms.android", "clr", "clr_loader", "pythonnet"]
    # keyring.backends.Windows НЕ исключаем: keyring грузит все бэкенды из entry points и логирует ошибку.

# .py рядом с .pyc: torch.jit/inspect/speechbrain lazy-импорт читают исходники и каталоги пакетов.
PYZ_PY = "pyz+py"
module_collection_mode = {
    "gigaam": PYZ_PY,
    "pyannote": PYZ_PY,
    "speechbrain": PYZ_PY,
    "lightning": PYZ_PY,
    "lightning_fabric": PYZ_PY,
    "pytorch_lightning": PYZ_PY,
    "torchmetrics": PYZ_PY,
    "torch_audiomentations": PYZ_PY,
    "torch_pitch_shift": PYZ_PY,
    "asteroid_filterbanks": PYZ_PY,
    "julius": PYZ_PY,
    "silero_vad": PYZ_PY,
    "torchaudio": PYZ_PY,
    "pytorch_metric_learning": PYZ_PY,
}

a = Analysis(  # noqa: F821
    [str(PACKAGING / "launcher.py")],
    pathex=[str(ROOT), *EXTRA_PATHS],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={
        "matplotlib": {"backends": ["Agg"]},
    },
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    module_collection_mode=module_collection_mode,
    optimize=0,  # docstrings нужны (hydra/omegaconf/argparse); assert'ы библиотек — тоже
)


def _drop(toc, patterns):
    return [entry for entry in toc if not any(re.search(p, entry[0].replace("\\", "/")) for p in patterns)]


# Не нужное во время выполнения: заголовки/cmake torch, тесты, dev-моки UI.
_DROP = [
    r"^torch/include/", r"^torch/share/", r"^torch/_inductor/codegen/.*\.(h|cpp)$",
    r"/tests?/", r"^bp_transcriber/ui/mock/", r"^bp_transcriber/ui/_dev_probe\.html$",
    r"^sklearn/datasets/(data|descr|images)/", r"^matplotlib/mpl-data/sample_data/",
]
a.datas = _drop(a.datas, _DROP)
a.binaries = _drop(a.binaries, [r"^torch/include/", r"/tests?/"])

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,  # strip на macOS ломает подписи библиотек; torch и так без отладочных символов
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,  # Apple Events открытия файлов приложение не обрабатывает
    target_arch="arm64" if IS_MAC else None,
    codesign_identity=os.environ.get("BP_CODESIGN_IDENTITY") or None,
    entitlements_file=os.environ.get("BP_ENTITLEMENTS") or None,
    icon=str(PACKAGING / "icons" / ("bp.ico" if IS_WIN else "bp.icns")),
    version=os.environ.get("BP_WIN_VERSION_FILE") or None if IS_WIN else None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)

if IS_MAC:
    app = BUNDLE(  # noqa: F821
        coll,
        name=f"{APP_NAME}.app",
        icon=str(PACKAGING / "icons" / "bp.icns"),
        bundle_identifier=BUNDLE_ID,
        version=VERSION,
        info_plist={
            "CFBundleName": APP_NAME,
            "CFBundleDisplayName": APP_NAME,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "CFBundleDevelopmentRegion": "ru",
            "LSMinimumSystemVersion": "12.0",
            "LSApplicationCategoryType": "public.app-category.productivity",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
            "NSHumanReadableCopyright": "© BestPractice",
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        },
    )
