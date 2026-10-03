#!/usr/bin/env python3
"""
Простое desktop GUI для GigaAM Transcriber (Tkinter, только стандартная библиотека).

Запуск: python gui_app.py (обычно через run_transcriber.command)
"""

import logging
import queue
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from gigaam_transcriber import GigaAMTranscriber
from gigaam_transcriber.exceptions import TranscriberError

AUDIO_VIDEO_TYPES = [
    ("Аудио/видео", "*.wav *.mp3 *.flac *.ogg *.m4a *.aac *.wma *.opus "
                     "*.mp4 *.mkv *.avi *.mov *.webm *.wmv *.flv"),
    ("Все файлы", "*.*"),
]

DIARIZATION_LABELS = {
    "pyannote (рекомендуется)": "pyannote",
    "hybrid (легче, но менее точно)": "hybrid",
    "без диаризации": "none",
}
FORMAT_LABELS = {
    "TXT": "txt",
    "JSON": "json",
    "SRT": "srt",
    "VTT": "vtt",
}


class QueueLogHandler(logging.Handler):
    """Пересылает записи логгера в thread-safe очередь для показа в GUI."""

    def __init__(self, log_queue: queue.Queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record: logging.LogRecord) -> None:
        self.log_queue.put(self.format(record))


class TranscriberGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Транскрибатор русской речи")

        self.log_queue: queue.Queue = queue.Queue()
        self.transcriber: GigaAMTranscriber | None = None
        self.result = None
        self.input_path: Path | None = None
        self.rename_vars: dict[str, tk.StringVar] = {}

        self._build_widgets()
        self._setup_logging()
        self._refit_window(center=True)
        self.root.after(150, self._drain_log_queue)

    def _refit_window(self, center: bool = False) -> None:
        """Подгоняет размер окна под текущее содержимое (например, после
        появления списка спикеров), чтобы нижние кнопки не уезжали за край."""
        self.root.update_idletasks()
        width = max(self.root.winfo_reqwidth(), self.root.winfo_width())
        height = max(self.root.winfo_reqheight(), self.root.winfo_height())
        self.root.minsize(width, height)
        if center:
            x = (self.root.winfo_screenwidth() - width) // 2
            y = (self.root.winfo_screenheight() - height) // 2
            self.root.geometry(f"{width}x{height}+{x}+{max(y, 0)}")
        else:
            self.root.geometry(f"{width}x{height}")

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_widgets(self) -> None:
        pad = {"padx": 8, "pady": 6}

        file_frame = ttk.Frame(self.root)
        file_frame.pack(fill="x", **pad)

        self.file_label = ttk.Label(file_frame, text="Файл не выбран", anchor="w")
        self.file_label.pack(side="left", fill="x", expand=True)

        ttk.Button(file_frame, text="Выбрать файл...", command=self._choose_file).pack(side="right")

        options_frame = ttk.LabelFrame(self.root, text="Параметры")
        options_frame.pack(fill="x", **pad)

        ttk.Label(options_frame, text="Диаризация:").grid(row=0, column=0, sticky="w", padx=6, pady=4)
        self.diarization_var = tk.StringVar(value="pyannote (рекомендуется)")
        ttk.Combobox(
            options_frame, textvariable=self.diarization_var,
            values=list(DIARIZATION_LABELS.keys()), state="readonly", width=28,
        ).grid(row=0, column=1, sticky="w", padx=6, pady=4)

        ttk.Label(options_frame, text="Кол-во спикеров (пусто = авто):").grid(
            row=0, column=2, sticky="w", padx=6, pady=4
        )
        self.speakers_var = tk.StringVar(value="")
        ttk.Entry(options_frame, textvariable=self.speakers_var, width=6).grid(
            row=0, column=3, sticky="w", padx=6, pady=4
        )

        ttk.Label(options_frame, text="Формат сохранения:").grid(row=1, column=0, sticky="w", padx=6, pady=4)
        self.format_var = tk.StringVar(value="TXT")
        ttk.Combobox(
            options_frame, textvariable=self.format_var,
            values=list(FORMAT_LABELS.keys()), state="readonly", width=10,
        ).grid(row=1, column=1, sticky="w", padx=6, pady=4)

        self.run_button = ttk.Button(options_frame, text="Транскрибировать", command=self._start_transcription)
        self.run_button.grid(row=1, column=3, sticky="e", padx=6, pady=4)

        self.progress = ttk.Progressbar(self.root, mode="indeterminate")
        self.progress.pack(fill="x", **pad)

        log_frame = ttk.LabelFrame(self.root, text="Лог")
        log_frame.pack(fill="both", expand=True, **pad)
        self.log_text = tk.Text(log_frame, height=10, state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True, padx=4, pady=4)

        result_frame = ttk.LabelFrame(self.root, text="Спикеры (можно переименовать)")
        result_frame.pack(fill="x", **pad)
        self.speakers_frame = ttk.Frame(result_frame)
        self.speakers_frame.pack(fill="x", padx=6, pady=4)

        bottom_frame = ttk.Frame(self.root)
        bottom_frame.pack(fill="x", **pad)
        ttk.Button(bottom_frame, text="Применить имена", command=self._apply_renames).pack(side="left")
        ttk.Button(bottom_frame, text="Сохранить...", command=self._save_result).pack(side="left", padx=6)
        ttk.Button(bottom_frame, text="Показать в Finder", command=self._reveal_output).pack(side="left")

        self.last_output_path: Path | None = None

    def _setup_logging(self) -> None:
        handler = QueueLogHandler(self.log_queue)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S"))
        logger = logging.getLogger("gigaam_transcriber")
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)

    def _drain_log_queue(self) -> None:
        while not self.log_queue.empty():
            line = self.log_queue.get_nowait()
            self.log_text.configure(state="normal")
            self.log_text.insert("end", line + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        self.root.after(150, self._drain_log_queue)

    def _log(self, message: str) -> None:
        self.log_queue.put(message)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def _choose_file(self) -> None:
        path = filedialog.askopenfilename(title="Выберите аудио или видео файл", filetypes=AUDIO_VIDEO_TYPES)
        if path:
            self.input_path = Path(path)
            self.file_label.configure(text=str(self.input_path))

    def _start_transcription(self) -> None:
        if self.input_path is None:
            messagebox.showwarning("Нет файла", "Сначала выберите аудио или видео файл.")
            return

        speakers_raw = self.speakers_var.get().strip()
        num_speakers = int(speakers_raw) if speakers_raw.isdigit() else None
        diarization_mode = DIARIZATION_LABELS[self.diarization_var.get()]

        self.run_button.configure(state="disabled")
        self.progress.start(12)
        self._log(f"Запуск транскрипции: {self.input_path.name} (диаризация={diarization_mode})")

        thread = threading.Thread(
            target=self._run_transcription,
            args=(self.input_path, diarization_mode, num_speakers),
            daemon=True,
        )
        thread.start()

    def _run_transcription(self, path: Path, diarization_mode: str, num_speakers: int | None) -> None:
        try:
            if self.transcriber is None:
                self._log("Загрузка модели GigaAM (в первый раз может занять пару минут)...")
                self.transcriber = GigaAMTranscriber(verbose=True)

            result = self.transcriber.transcribe(
                path,
                diarization=diarization_mode,
                num_speakers=num_speakers,
            )
            self.root.after(0, self._on_transcription_done, result)
        except TranscriberError as exc:
            self.root.after(0, self._on_transcription_error, str(exc))
        except Exception as exc:  # noqa: BLE001 - показать любую ошибку пользователю
            self.root.after(0, self._on_transcription_error, str(exc))

    def _on_transcription_done(self, result) -> None:
        self.result = result
        self.progress.stop()
        self.run_button.configure(state="normal")
        self._log(f"Готово: {len(result.segments)} сегментов, {result.processing_time:.1f}с обработки.")
        self._log("── Текст ──")
        self._log(result.to_txt())
        self._log("───────────")

        for widget in self.speakers_frame.winfo_children():
            widget.destroy()
        self.rename_vars.clear()

        speakers = result.get_speakers()
        if not speakers:
            ttk.Label(self.speakers_frame, text="Диаризация не использовалась — один общий текст.").pack(anchor="w")
        else:
            for speaker in speakers:
                row = ttk.Frame(self.speakers_frame)
                row.pack(fill="x", pady=2)
                ttk.Label(row, text=speaker, width=20).pack(side="left")
                ttk.Label(row, text="→").pack(side="left", padx=4)
                var = tk.StringVar(value=speaker)
                ttk.Entry(row, textvariable=var, width=30).pack(side="left")
                self.rename_vars[speaker] = var

        self._refit_window()

    def _on_transcription_error(self, message: str) -> None:
        self.progress.stop()
        self.run_button.configure(state="normal")
        self._log(f"ОШИБКА: {message}")
        messagebox.showerror("Ошибка транскрипции", message)

    def _apply_renames(self) -> None:
        if self.result is None:
            return
        rename_map = {
            old: var.get().strip() or old
            for old, var in self.rename_vars.items()
        }
        for seg in self.result.segments:
            if seg.speaker in rename_map:
                seg.speaker = rename_map[seg.speaker]
        self._log("Имена спикеров применены.")
        self._log("── Текст ──")
        self._log(self.result.to_txt())
        self._log("───────────")

    def _save_result(self) -> None:
        if self.result is None:
            messagebox.showwarning("Нет результата", "Сначала выполните транскрипцию.")
            return
        fmt_label = self.format_var.get()
        fmt = FORMAT_LABELS[fmt_label]
        default_name = (self.input_path.stem if self.input_path else "transcript") + f".{fmt}"
        path = filedialog.asksaveasfilename(
            title="Сохранить результат",
            initialfile=default_name,
            defaultextension=f".{fmt}",
        )
        if not path:
            return
        saved_path = self.result.save(path, format=fmt)
        self.last_output_path = Path(saved_path)
        self._log(f"Сохранено: {saved_path}")

    def _reveal_output(self) -> None:
        if self.last_output_path is None:
            messagebox.showinfo("Нет файла", "Сначала сохраните результат.")
            return
        subprocess.run(["open", "-R", str(self.last_output_path)], check=False)


def main() -> None:
    root = tk.Tk()
    TranscriberGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
