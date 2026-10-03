# BP Transcriber — контракты между модулями

Этот файл — источник правды для параллельных треков. Меняет его только главная сессия.
Все тексты для пользователя — на русском. Код/комментарии — в стиле существующего пакета.

## 0. Общие правила
- Python 3.12, `from __future__ import annotations`, dataclasses, type hints.
- Ничего не печатать в stdout (во frozen-сборке Windows stdout = None); только `logging.getLogger(__name__)`.
- Никогда не логировать и не возвращать в JS значение HF-токена.
- Тяжёлые импорты (torch, pyannote, gigaam, silero_vad, speechbrain) — лениво внутри функций/методов,
  чтобы `import gigaam_transcriber` и запуск окна были быстрыми.
- Перед импортом pyannote: `os.environ.setdefault("PYANNOTE_METRICS_ENABLED", "false")`,
  `HF_HUB_DISABLE_TELEMETRY=1`. Перед импортом torch: `PYTORCH_ENABLE_MPS_FALLBACK=1`.
- Аудио внутри пайплайна: моно, 16 кГц. Время — секунды (float).

## 1. Ядро: `gigaam_transcriber/`

### 1.1 `progress.py` (общие типы, трек A1)
```python
STAGES = ("load", "decode", "vad", "asr", "diarize", "finalize")

@dataclass
class ProgressEvent:
    stage: str               # один из STAGES
    stage_progress: float    # 0..1 внутри этапа
    progress: float          # 0..1 общий (взвешенный)
    message: str             # человекочитаемо, по-русски
    eta_s: float | None = None

ProgressCallback = Callable[[ProgressEvent], None]

class Cancelled(TranscriberError): ...      # TranscriberError из exceptions.py

class CancelToken:
    def cancel(self) -> None
    @property
    def cancelled(self) -> bool
    def raise_if_cancelled(self) -> None     # raise Cancelled("Отменено пользователем")
```
Функции низкого уровня принимают `on_progress: Callable[[float], None] | None` (доля 0..1 своего этапа)
и `cancel: CancelToken | None`. Взвешивание в общий прогресс делает только `pipeline.py`.

### 1.2 `audio_io.py` (A1)
```python
SAMPLE_RATE = 16000

@dataclass
class DecodedAudio:
    pcm: np.ndarray          # int16, shape (n,), моно 16 кГц
    source: Path
    @property
    def duration(self) -> float
    def float32(self, start: float = 0.0, end: float | None = None) -> np.ndarray   # [-1, 1], копия куска
    def torch_waveform(self) -> "torch.Tensor"   # float32 (1, n) для pyannote

def find_ffmpeg() -> str            # 1) bundled bin (paths frozen: sys._MEIPASS/bin), 2) env BP_FFMPEG, 3) shutil.which; иначе FFmpegNotFoundError
def probe_duration(path) -> float | None   # из stderr ffmpeg ("Duration:"), без ffprobe; None если неизвестно
def decode(path, *, on_progress=None, cancel=None) -> DecodedAudio
    # один процесс: ffmpeg -nostdin -hide_banner -i <path> -vn -ac 1 -ar 16000 -f s16le -acodec pcm_s16le pipe:1
    # прогресс по `-progress pipe:2` (out_time_us / duration); отмена = kill; Windows: CREATE_NO_WINDOW
    # ошибки: UnsupportedFormatError / AudioProcessingError с понятным текстом
def encode_preview(audio: DecodedAudio, out_path: Path, *, cancel=None) -> Path
    # PCM -> stdin ffmpeg -> AAC 48 кбит/с .m4a (для плеера в UI; работает в WKWebView и WebView2)
```

### 1.3 `vad.py` (A1)
```python
@dataclass
class Chunk:
    start: float
    end: float

def detect_speech(audio: DecodedAudio, *, on_progress=None, cancel=None) -> list[tuple[float, float]]
    # Silero VAD (silero_vad.load_silero_vad(), JIT, модель кэшируется на процесс)
def make_chunks(speech, *, min_duration=15.0, max_duration=22.0, strict_limit=25.0, min_keep=0.2) -> list[Chunk]
    # порт логики GigaAM/gigaam/vad_utils.py:98-136; ни один чанк не длиннее strict_limit
```

### 1.4 `device.py` (A1)
```python
def pick_device(pref: str = "auto") -> "torch.device"   # pref: "auto" | "cpu" | "gpu"; auto/gpu: cuda > mps > cpu
def device_label(dev) -> str                              # "Apple GPU (MPS)", "NVIDIA <name>", "CPU"
def available_options() -> list[dict]                     # [{"id":"auto","label":...}, {"id":"cpu"...}, {"id":"gpu","label":..., "available": bool}]
```

### 1.5 `model_store.py` (A1)
```python
DEFAULT_MODEL = "v3_e2e_rnnt"
def models_root() -> Path    # ~/.cache/gigaam если там уже есть ckpt нужной модели, иначе platformdirs.user_cache_dir("BP Transcriber","BestPractice")/gigaam
def is_model_available(name=DEFAULT_MODEL) -> bool       # ckpt + tokenizer (если нужен) существуют; md5 НЕ считаем тут (дорого)
def ensure_model(name=DEFAULT_MODEL, *, on_progress: Callable[[int, int], None] | None = None, cancel=None) -> Path
    # возвращает download_root для gigaam.load_model; качает в .part с потоковым md5, атомарный rename;
    # URL: GigaAM _URL_DIR, фолбэк-зеркало MIRROR_URL = "https://github.com/Isalin84/bp-transcriber/releases/download/models-v1"
    # md5 сверяется с gigaam._MODEL_HASHES; маркер "<name>.verified" рядом, чтобы не пересчитывать md5 при каждом старте
```

### 1.6 `asr_engine.py` (A2) — единственный файл, знающий внутренности GigaAM
```python
@dataclass
class AsrSegment:
    start: float
    end: float
    text: str
    words: list[WordSegment]    # WordSegment из data_models (word, start, end), абсолютное время

class GigaAMEngine:
    def __init__(self, model_name=DEFAULT_MODEL, device: str = "auto")
    def load(self, *, on_progress=None, cancel=None) -> None   # ensure_model + gigaam.load_model(download_root=...) + smoke-тест, фолбэк на CPU
    @property
    def loaded(self) -> bool
    @property
    def device_label(self) -> str
    def transcribe(self, audio: DecodedAudio, chunks: list[Chunk], *, on_progress=None, cancel=None) -> list[AsrSegment]
    def unload(self) -> None
```

### 1.7 `diarization.py` (A2) — переписывается; старые классы можно оставить для CLI до интеграции
```python
@dataclass
class SpeakerTurn:
    start: float
    end: float
    speaker: str          # "SPEAKER_00", "SPEAKER_01", ...

PYANNOTE_REPOS = ["pyannote/speaker-diarization-community-1", "pyannote/segmentation-3.0", "pyannote/speaker-diarization-3.1"]

@dataclass
class TokenCheck:
    ok: bool
    state: str            # "ok" | "missing" | "invalid" | "terms_not_accepted" | "network"
    user: str | None
    repos: list[dict]     # [{"id", "url", "ok": bool}]
    message: str          # по-русски
    def to_dict(self) -> dict

def check_token(token: str | None) -> TokenCheck
def pyannote_cached() -> bool   # модели диаризации уже в HF-кэше (local_files_only)

class PyannoteDiarizer:
    def __init__(self, token: str | None, device: str = "auto")
    def diarize(self, audio: DecodedAudio, *, num_speakers=None, min_speakers=None, max_speakers=None,
                on_progress=None, cancel=None) -> list[SpeakerTurn]

class HybridDiarizer:   # без токена
    def __init__(self, device: str = "auto")
    def diarize(self, audio: DecodedAudio, speech: list[tuple[float, float]], *, num_speakers=None,
                on_progress=None, cancel=None) -> list[SpeakerTurn]

def assign_words(words: list[WordSegment], turns: list[SpeakerTurn]) -> list[str | None]   # спикер на каждое слово
def regroup(asr: list[AsrSegment], speakers_per_word: list[str | None] | None, *,
            max_gap=1.0, soft_max=20.0, hard_max=40.0) -> list[TranscriptionSegment]
    # если speakers_per_word None — сегменты без спикера, всё равно режем по паузам/длине на границе предложения
def relabel(segments) -> None   # SPEAKER_xx -> "Спикер 1", "Спикер 2" в порядке первого появления
```

### 1.8 `pipeline.py` (A2, интеграция)
```python
@dataclass
class PipelineOptions:
    diarization: str = "auto"        # "auto" | "pyannote" | "hybrid" | "none"; auto = pyannote если токен есть/модели в кэше, иначе hybrid
    num_speakers: int | None = None
    min_speakers: int | None = None
    max_speakers: int | None = None
    preview_path: Path | None = None # если задан — encode_preview туда

class TranscriptionPipeline:
    def __init__(self, model_name=DEFAULT_MODEL, device: str = "auto", hf_token: str | None = None)
    def set_token(self, token: str | None) -> None
    def set_device(self, device: str) -> None    # перезагрузит модель при следующем run
    def prepare(self, *, on_event: ProgressCallback | None = None, cancel=None) -> None   # загрузить GigaAM (этап "load")
    def run(self, path, options: PipelineOptions, *, on_event=None, cancel=None) -> TranscriptionResult
    def close(self) -> None
```
Веса этапов в `progress`: load 0 (отдельно), decode 0.05, vad 0.05, asr 0.60, diarize 0.30, finalize ~0
(без диаризации веса нормируются на оставшиеся этапы). `eta_s` — по скорости текущего этапа.
`TranscriptionResult.metadata` содержит: `source`, `device`, `diarization` (фактический режим),
`num_speakers`, `pipeline_version: 2`, `timings: {stage: seconds}`.
`TranscriptionSegment.words` всегда заполнены (если модель вернула слова).

## 2. Приложение: JS ⇄ Python (`bp_transcriber/api.py` ⇄ `bp_transcriber/ui/`)

Все методы — `window.pywebview.api.<name>(...)` → Promise с JSON. Ошибки: Promise reject с `{message}`.
События Python → JS: `window.bp.onEvent(event)` (вызывается через `evaluate_js`, ≤10 Гц на задачу).
UI ждёт событие `pywebviewready` перед первым вызовом API.

### 2.1 Типы
```ts
type Theme = "system" | "dark" | "light"
type Diarization = "auto" | "pyannote" | "hybrid" | "none"
type Format = "txt" | "md" | "docx" | "srt" | "vtt" | "json"

interface Settings {
  theme: Theme; device: "auto" | "cpu" | "gpu";
  diarization: Diarization; num_speakers: number | null;
  autosave: boolean; autosave_dir: string | null;   // null = рядом с исходным файлом
  autosave_formats: Format[]; include_timestamps: boolean;
  hf_token_set: boolean;                             // только флаг, сам токен в JS не попадает
}
interface TokenCheck { ok: boolean; state: "ok"|"missing"|"invalid"|"terms_not_accepted"|"network";
  user: string | null; repos: {id: string; url: string; ok: boolean | null}[]   // null = не проверялся; message: string }
interface ModelsStatus {
  gigaam: { available: boolean; size_bytes: number; downloading: boolean; progress: number };
  pyannote: { cached: boolean; token_ok: boolean | null };
}
interface AppState {
  version: string; os: "macos" | "windows" | "linux";
  settings: Settings; models: ModelsStatus;
  devices: {id: string; label: string; available: boolean}[];
  token_import: { source: ".env" | "env" | "hf_cache" } | null;   // найден существующий токен — предложить импорт
  first_run: boolean;
}
interface JobOptions { diarization: Diarization; num_speakers: number | null }
interface Job {
  id: string; file_name: string; path: string;
  status: "queued" | "running" | "done" | "error" | "cancelled";
  stage: "load"|"decode"|"vad"|"asr"|"diarize"|"finalize"|null;
  progress: number; stage_progress: number; message: string; eta_s: number | null;
  created_at: number; started_at: number | null; finished_at: number | null;   // unix seconds
  error: string | null; transcript_id: string | null; options: JobOptions;
  duration: number | null;          // секунды, probe_duration; null пока неизвестно
}
interface Word { w: string; s: number; e: number }
interface Segment { index: number; start: number; end: number; speaker: string | null; text: string; words: Word[] }
interface Speaker { id: string; name: string; color: number }        // color: индекс палитры 0..7
interface Transcript {
  id: string; file_name: string; source_path: string; duration: number;
  created_at: number; processing_time: number; device: string; diarization: string;
  speakers: Speaker[]; segments: Segment[];
  media_url: string | null;        // http://127.0.0.1:<port>/media/<id>?k=<token>, поддерживает Range
}
interface HistoryItem { id: string; file_name: string; created_at: number; duration: number;
  speakers: number; preview: string }
```

### 2.2 Методы
| Метод | Возврат | Примечание |
|---|---|---|
| `get_state()` | `AppState` | первый вызов UI |
| `complete_onboarding()` | `AppState` | сохраняет `onboarding_done`, дальше `first_run=false` |
| `pick_files()` | `string[]` | нативный диалог, фильтр аудио/видео, мультивыбор |
| `choose_folder()` | `string \| null` | для autosave_dir |
| `enqueue(paths, options)` | `Job[]` | |
| `list_jobs()` | `Job[]` | |
| `cancel_job(id)` | `boolean` | |
| `remove_job(id)` | `boolean` | только не running |
| `get_settings()` / `save_settings(partial)` | `Settings` | |
| `set_hf_token(token)` | `TokenCheck` | сохраняет в keyring и проверяет |
| `import_hf_token()` | `TokenCheck` | из найденного источника |
| `clear_hf_token()` | `Settings` | |
| `check_hf_token()` | `TokenCheck` | |
| `models_status()` | `ModelsStatus` | |
| `download_models()` | `boolean` | прогресс через события `model_download` |
| `list_history(query?)` | `HistoryItem[]` | новые сверху, поиск по имени и тексту |
| `load_transcript(id)` | `Transcript` | |
| `rename_speaker(id, speaker_id, name)` | `Transcript` | |
| `edit_segment(id, index, text)` | `Transcript` | слова сегмента пересобираются без таймингов → `words: []` |
| `delete_transcript(id)` | `boolean` | |
| `export(id, format, path?)` | `{path: string} \| null` | без path — диалог сохранения; null если отменили |
| `copy_text(id, with_timestamps)` | `boolean` | в системный буфер обмена |
| `reveal(path)` | `boolean` | Finder / Explorer |
| `open_url(url)` | `boolean` | внешний браузер |

### 2.3 События
```ts
{ type: "job_update", job: Job }
{ type: "job_done", job: Job, transcript_id: string }
{ type: "model_download", status: "downloading"|"done"|"error", progress: number, downloaded: number, total: number, message?: string }
{ type: "files_dropped", paths: string[] }        // drag&drop из ОС (Python-обработчик drop)
{ type: "toast", level: "info"|"success"|"warning"|"error", message: string }
```

## 3. Хранение (`bp_transcriber/`)
- Настройки: `platformdirs.user_config_dir("BP Transcriber", "BestPractice")/settings.json`.
- Токен: `keyring` service `"BP Transcriber"`, username `"hf_token"`; фолбэк — файл с правами 600 рядом с settings.
- История: `user_data_dir/history/<id>/{meta.json, transcript.json, preview.m4a}` + `index.json`.
  `transcript.json` = `TranscriptionResult.to_json()` + `"speakers": [{id, name, color}]`;
  `segment.speaker` хранит id спикера, имена применяются при выдаче/экспорте.
