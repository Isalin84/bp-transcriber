// Browser mock of window.pywebview.api (contracts.md §2). Installed only when no real
// backend shows up within ~300 ms, or when the URL has ?mock=1.
// Extra URL flags for manual testing: ?first=1 (onboarding), ?nomodel=1 (model not
// downloaded), ?notoken=1, ?big=1 (≈1500-segment transcript), ?nomedia=1 (no player).

const params = new URLSearchParams(location.search);
const flag = (k) => params.get(k) === '1';

function install() {
  if (window.pywebview?.api) return;
  window.__bpMock = true;
  const api = createMockApi();
  window.pywebview = { api };
  window.dispatchEvent(new Event('pywebviewready'));
  console.info('[bp] mock API installed');
}

// ---------------------------------------------------------------------------

const DIALOGUE = [
  [0, 'Коллеги, давайте начнём. Сегодня обсуждаем пилот по культуре безопасности на второй площадке и то, как нам помогает нейросеть с анализом инцидентов.'],
  [1, 'Да, я подготовила цифры за квартал. Количество зарегистрированных потенциально опасных ситуаций выросло почти вдвое, но это хороший знак.'],
  [0, 'Поясни, почему хороший.'],
  [1, 'Потому что раньше люди просто не сообщали. Сейчас сообщают через чат-бота, это занимает двадцать секунд, и регистрация перестала быть наказанием.'],
  [2, 'Подтверждаю со стороны производства. Мастера сначала ворчали, а теперь сами просят добавить бота в ночную смену.'],
  [0, 'Отлично. Алексей, что с разбором причин? Удалось ли ускорить анализ корневых причин?'],
  [2, 'Удалось. Модель группирует похожие сообщения и подсказывает типовые причины, но финальное решение всегда за комиссией. Мы это специально оставили людям.'],
  [1, 'Важно, что подсказки формулируются по нашему стандарту, а не абстрактно. Мы дообучили промпт на наших же отчётах за три года.'],
  [0, 'Сколько времени теперь занимает один разбор?'],
  [2, 'В среднем полтора часа вместо четырёх. Самое долгое по-прежнему интервью с участниками, но и его теперь транскрибируем автоматически.'],
  [1, 'Кстати, про транскрибацию. Запись совещаний с подрядчиками оказалась очень полезной, потому что договорённости больше не теряются.'],
  [0, 'Давайте зафиксируем это как отдельный результат пилота. Марина, подготовишь короткую справку для директора по производству?'],
  [1, 'Подготовлю к четвергу. Нужны ли примеры конкретных случаев или достаточно статистики?'],
  [0, 'Лучше два-три живых примера. Цифры без историй не убеждают.'],
  [2, 'Могу дать случай с погрузчиком на складе готовой продукции. Там бот поймал повторяющуюся ситуацию, которую никто не связывал в одну картину.'],
  [0, 'Расскажи подробнее, это как раз хороший пример.'],
  [2, 'Три разных смены сообщали о плохой видимости на одном и том же повороте. По отдельности это мелочи, а вместе получилась системная проблема с освещением.'],
  [1, 'И решение стоило копейки, просто перевесили светильник и нанесли разметку.'],
  [0, 'Вот это и есть культура безопасности в действии. Что по обучению персонала?'],
  [1, 'Запустили короткие видеоуроки, сгенерированные по нашим инструкциям. Прохождение выросло с сорока до восьмидесяти процентов.'],
  [2, 'Люди смотрят их прямо с телефона в перерыве. Пять минут вместо часового инструктажа в классе.'],
  [0, 'Но классический инструктаж мы не отменяем, верно?'],
  [1, 'Не отменяем. Видео готовит к нему, а не заменяет. Так требует и регламент, и здравый смысл.'],
  [0, 'Хорошо. Какие риски вы видите при масштабировании на остальные площадки?'],
  [2, 'Главный риск — формальность. Если руководители площадок воспримут это как отчётность, люди перестанут сообщать.'],
  [1, 'Поэтому в плане внедрения первым пунктом стоит встреча с директорами площадок, а не установка софта.'],
  [0, 'Согласен. Технологии вторичны, первична позиция руководителя.'],
  [2, 'Ещё один момент: данные должны оставаться внутри компании. Мы проверили, что модели работают локально, ничего не уходит наружу.'],
  [1, 'Это было требованием службы информационной безопасности, и мы его закрыли.'],
  [0, 'Отлично. Что нужно от меня для следующего этапа?'],
  [1, 'Решение по бюджету на оборудование для трёх площадок и письмо директорам о старте программы.'],
  [2, 'И желательно личное участие в первой встрече на каждой площадке. Это сильно меняет отношение.'],
  [0, 'Запланируем. Марина, добавь в календарь на ноябрь по неделе на площадку.'],
  [1, 'Сделаю. Ещё предлагаю завести общий дашборд, чтобы динамика была видна всем, а не только нам троим.'],
  [0, 'Поддерживаю, только без лишних метрик. Три-четыре показателя, которые реально отражают культуру.'],
  [2, 'Количество сообщений, доля закрытых в срок, время разбора и вовлечённость в обучение. Этого достаточно.'],
  [1, 'Записала. Расшифровку встречи разошлю сегодня, чтобы все увидели договорённости.'],
  [0, 'Спасибо, коллеги. Хороший результат за квартал, продолжаем в том же духе.'],
  [2, 'Спасибо. До связи.'],
  [1, 'До свидания.'],
];

function buildSegments(repeat = 1) {
  const segs = [];
  let t = 0.8, index = 0;
  for (let r = 0; r < repeat; r++) {
    for (const [spk, text] of DIALOGUE) {
      const tokens = text.split(' ');
      const words = [];
      let wt = t;
      for (const w of tokens) {
        const d = 0.18 + Math.min(0.5, w.replace(/[^а-яёa-z]/gi, '').length * 0.055);
        words.push({ w, s: +wt.toFixed(2), e: +(wt + d).toFixed(2) });
        wt += d + 0.07;
      }
      segs.push({ index: index++, start: +t.toFixed(2), end: +wt.toFixed(2), speaker: `spk${spk}`, text, words });
      t = wt + 0.45 + (spk === 0 ? 0.3 : 0);
    }
  }
  return segs;
}

function makeTranscript(id, fileName, createdAt, repeat = 1) {
  const segments = buildSegments(repeat);
  return {
    id, file_name: fileName, source_path: `/Users/ivan/Записи/${fileName}`,
    duration: segments[segments.length - 1].end + 1.2,
    created_at: createdAt, processing_time: 42 + 30 * repeat, device: 'Apple GPU (MPS)', diarization: 'pyannote',
    speakers: [{ id: 'spk0', name: 'Спикер 1', color: 0 }, { id: 'spk1', name: 'Спикер 2', color: 1 }, { id: 'spk2', name: 'Спикер 3', color: 2 }],
    segments,
    media_url: flag('nomedia') ? null : 'mock/sample.m4a',
  };
}

function createMockApi() {
  const now = () => Date.now() / 1000;
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const emit = (evt) => window.bp?.onEvent?.(evt);

  let settings = {
    theme: params.get('theme') || 'system', device: 'auto', diarization: 'auto', num_speakers: null,
    autosave: false, autosave_dir: null, autosave_formats: ['docx'], include_timestamps: true,
    hf_token_set: !flag('notoken'),
  };
  let tokenState = settings.hf_token_set ? 'ok' : 'missing';
  let models = {
    gigaam: { available: !flag('nomodel') && !flag('first'), size_bytes: 471859200, downloading: false, progress: 0 },
    pyannote: { cached: settings.hf_token_set, token_ok: settings.hf_token_set ? true : null },
  };

  const transcripts = new Map();
  const t0 = now();
  transcripts.set('tr-1', makeTranscript('tr-1', 'Совещание по культуре безопасности.m4a', t0 - 3600 * 2, flag('big') ? 38 : 1));
  transcripts.set('tr-2', makeTranscript('tr-2', 'Интервью с мастером смены.mp3', t0 - 86400 - 1800));
  transcripts.set('tr-3', makeTranscript('tr-3', 'Лекция — Генеративный ИИ в промышленности.mp4', t0 - 86400 * 6));
  transcripts.get('tr-2').speakers.pop();
  transcripts.get('tr-2').segments = transcripts.get('tr-2').segments.filter((s) => s.speaker !== 'spk2');
  transcripts.get('tr-3').diarization = 'none';
  transcripts.get('tr-3').speakers = [];
  transcripts.get('tr-3').segments.forEach((s) => { s.speaker = null; });
  transcripts.get('tr-3').device = 'CPU';

  const jobs = [];
  let jobSeq = 1, trSeq = 10;
  let modelLoaded = false;
  let processing = false;

  const toHistory = (t) => ({
    id: t.id, file_name: t.file_name, created_at: t.created_at, duration: t.duration,
    speakers: t.speakers.length, preview: t.segments.slice(0, 2).map((s) => s.text).join(' ').slice(0, 160) + '…',
  });

  const tokenCheck = (state, user = 'ivan-salin') => ({
    ok: state === 'ok', state, user: state === 'ok' || state === 'terms_not_accepted' ? user : null,
    repos: [
      { id: 'pyannote/speaker-diarization-community-1', url: 'https://huggingface.co/pyannote/speaker-diarization-community-1', ok: state === 'ok' },
      { id: 'pyannote/segmentation-3.0', url: 'https://huggingface.co/pyannote/segmentation-3.0', ok: state === 'ok' || state === 'terms_not_accepted' },
    ],
    message: {
      ok: 'Токен действителен, доступ к моделям pyannote подтверждён.',
      missing: 'Токен не задан.',
      invalid: 'HuggingFace не принял этот токен. Проверьте, что скопировали его целиком.',
      terms_not_accepted: 'Токен работает, но не приняты условия модели pyannote/speaker-diarization-community-1.',
      network: 'Не удалось связаться с huggingface.co. Проверьте подключение к интернету.',
    }[state],
  });

  async function processQueue() {
    if (processing) return;
    processing = true;
    while (true) {
      const job = jobs.find((j) => j.status === 'queued');
      if (!job) break;
      job.status = 'running'; job.started_at = now();
      const plan = [
        ['load', modelLoaded ? 0 : 1600, 'Загружаем модель GigaAM'],
        ['decode', 900, 'Декодируем аудио'],
        ['vad', 700, 'Ищем речь'],
        ['asr', 3800, 'Распознаём речь'],
        ['diarize', job.options.diarization === 'none' ? 0 : 2400, 'Разделяем по спикерам'],
        ['finalize', 250, 'Собираем транскрипт'],
      ];
      const totalMs = plan.reduce((a, [, ms]) => a + ms, 0);
      const weights = { load: 0, decode: 0.05, vad: 0.05, asr: 0.6, diarize: job.options.diarization === 'none' ? 0 : 0.3, finalize: 0 };
      const wsum = Object.values(weights).reduce((a, b) => a + b, 0);
      let base = 0, elapsed = 0, failed = false;
      for (const [stage, ms, message] of plan) {
        if (!ms) { base += weights[stage] / wsum; continue; }
        const start = performance.now();
        while (performance.now() - start < ms) {
          if (job.status === 'cancelled') break;
          const sp = Math.min(1, (performance.now() - start) / ms);
          job.stage = stage; job.stage_progress = sp; job.message = message;
          job.progress = Math.min(0.99, base + (weights[stage] / wsum) * sp);
          job.eta_s = Math.max(1, (totalMs - elapsed - sp * ms) / 1000);
          emit({ type: 'job_update', job: { ...job } });
          await sleep(100);
        }
        if (job.status === 'cancelled') break;
        if (job.file_name.includes('битый')) { failed = true; break; }
        elapsed += ms; base += weights[stage] / wsum;
        if (stage === 'load') modelLoaded = true;
      }
      job.finished_at = now();
      if (job.status === 'cancelled') { job.stage = null; job.eta_s = null; emit({ type: 'job_update', job: { ...job } }); continue; }
      if (failed) {
        job.status = 'error'; job.error = 'FFmpeg не смог прочитать файл: похоже, он повреждён или это не аудио. Попробуйте пересохранить его в другом формате.';
        job.stage = null; job.eta_s = null;
        emit({ type: 'job_update', job: { ...job } });
        continue;
      }
      const id = `tr-${trSeq++}`;
      const t = makeTranscript(id, job.file_name, now());
      t.processing_time = job.finished_at - job.started_at;
      if (job.options.diarization === 'none') { t.diarization = 'none'; t.speakers = []; t.segments.forEach((s) => { s.speaker = null; }); }
      else t.diarization = settings.hf_token_set && job.options.diarization !== 'hybrid' ? 'pyannote' : 'hybrid';
      transcripts.set(id, t);
      job.status = 'done'; job.stage = 'finalize'; job.progress = 1; job.stage_progress = 1; job.eta_s = null; job.message = 'Готово'; job.transcript_id = id;
      emit({ type: 'job_done', job: { ...job }, transcript_id: id });
      if (settings.autosave) emit({ type: 'toast', level: 'info', message: `Сохранено: ${job.file_name.replace(/\.[^.]+$/, '')}.docx` });
    }
    processing = false;
  }

  return {
    async complete_onboarding() { return this.get_state(); },
    async get_state() {
      await sleep(80);
      return {
        version: '1.0.0-beta', os: navigator.platform.startsWith('Mac') ? 'macos' : navigator.platform.startsWith('Win') ? 'windows' : 'linux',
        settings: { ...settings }, models: JSON.parse(JSON.stringify(models)),
        devices: [{ id: 'auto', label: 'Автоматически (Apple GPU)', available: true }, { id: 'gpu', label: 'Apple GPU (MPS)', available: true }, { id: 'cpu', label: 'Процессор (CPU)', available: true }],
        token_import: settings.hf_token_set ? null : { source: '.env' },
        first_run: flag('first'),
      };
    },
    async pick_files() {
      await sleep(400);
      const names = ['Планёрка 14 октября.m4a', 'Интервью кандидата.mp3', 'Запись вебинара.mp4'];
      const pick = names[Math.floor(Math.random() * names.length)];
      return [`/Users/ivan/Записи/${pick}`];
    },
    async choose_folder() { await sleep(300); return '/Users/ivan/Documents/Транскрипты'; },
    async enqueue(paths, options) {
      const created = paths.map((p) => ({
        id: `job-${jobSeq++}`, file_name: p.split(/[\\/]/).pop(), path: p, status: 'queued', stage: null,
        progress: 0, stage_progress: 0, message: 'В очереди', eta_s: null, created_at: now(), started_at: null, finished_at: null,
        error: null, transcript_id: null, options: { ...options }, duration: 180 + Math.round(Math.random() * 2400),
      }));
      jobs.push(...created); // FIFO: processed in the order they were added
      setTimeout(processQueue, 150);
      return created.map((j) => ({ ...j }));
    },
    async list_jobs() { return jobs.map((j) => ({ ...j })).reverse(); },
    async cancel_job(id) {
      const j = jobs.find((x) => x.id === id);
      if (!j || (j.status !== 'queued' && j.status !== 'running')) return false;
      const wasQueued = j.status === 'queued';
      j.status = 'cancelled'; j.finished_at = now();
      if (wasQueued) emit({ type: 'job_update', job: { ...j } }); // running jobs report it from the loop
      return true;
    },
    async remove_job(id) { const i = jobs.findIndex((x) => x.id === id); if (i < 0 || jobs[i].status === 'running') return false; jobs.splice(i, 1); return true; },
    async get_settings() { return { ...settings }; },
    async save_settings(partial) { settings = { ...settings, ...partial }; return { ...settings }; },
    async set_hf_token(token) {
      await sleep(900);
      if (/^hf_[A-Za-z0-9]{8,}$/.test(token)) tokenState = token.includes('terms') ? 'terms_not_accepted' : 'ok';
      else if (token === 'net') tokenState = 'network';
      else tokenState = 'invalid';
      settings.hf_token_set = tokenState !== 'invalid';
      models.pyannote.token_ok = tokenState === 'ok';
      return tokenCheck(tokenState);
    },
    async import_hf_token() { await sleep(700); tokenState = 'ok'; settings.hf_token_set = true; return tokenCheck('ok'); },
    async clear_hf_token() { tokenState = 'missing'; settings.hf_token_set = false; models.pyannote.token_ok = null; return { ...settings }; },
    async check_hf_token() { await sleep(600); return tokenCheck(settings.hf_token_set ? tokenState : 'missing'); },
    async models_status() { return JSON.parse(JSON.stringify(models)); },
    async download_models() {
      if (models.gigaam.available || models.gigaam.downloading) return true;
      models.gigaam.downloading = true;
      const total = models.gigaam.size_bytes;
      (async () => {
        for (let p = 0; p < 1; p += 0.012 + Math.random() * 0.02) {
          await sleep(90);
          models.gigaam.progress = p;
          emit({ type: 'model_download', status: 'downloading', progress: p, downloaded: Math.round(total * p), total, message: `${(4.2 + Math.random() * 3).toFixed(1)} МБ/с` });
        }
        models.gigaam = { ...models.gigaam, available: true, downloading: false, progress: 1 };
        emit({ type: 'model_download', status: 'done', progress: 1, downloaded: total, total });
      })();
      return true;
    },
    async list_history(query) {
      await sleep(120);
      const q = (query || '').toLowerCase();
      return [...transcripts.values()].sort((a, b) => b.created_at - a.created_at)
        .filter((t) => !q || t.file_name.toLowerCase().includes(q) || t.segments.some((s) => s.text.toLowerCase().includes(q)))
        .map(toHistory);
    },
    async load_transcript(id) { await sleep(150); const t = transcripts.get(id); if (!t) throw new Error('Транскрипт не найден'); return JSON.parse(JSON.stringify(t)); },
    async rename_speaker(id, speakerId, name) {
      await sleep(150); const t = transcripts.get(id); const sp = t.speakers.find((s) => s.id === speakerId); if (sp) sp.name = name; return JSON.parse(JSON.stringify(t));
    },
    async edit_segment(id, index, text) {
      await sleep(150); const t = transcripts.get(id); const s = t.segments[index]; if (s) { s.text = text; s.words = []; } return JSON.parse(JSON.stringify(t));
    },
    async delete_transcript(id) { await sleep(150); return transcripts.delete(id); },
    async export(id, format) { await sleep(500); const t = transcripts.get(id); return { path: `/Users/ivan/Documents/Транскрипты/${t.file_name.replace(/\.[^.]+$/, '')}.${format}` }; },
    async copy_text(id, withTs) {
      const t = transcripts.get(id);
      const text = t.segments.map((s) => {
        const sp = t.speakers.find((x) => x.id === s.speaker);
        return (withTs ? `[${fmt(s.start)}] ` : '') + (sp ? sp.name + ': ' : '') + s.text;
      }).join('\n');
      try { await navigator.clipboard.writeText(text); } catch (_) { /* clipboard may be blocked without a gesture */ }
      return true;
    },
    async reveal(path) { console.info('[mock] reveal', path); return true; },
    async open_url(url) { window.open(url, '_blank', 'noopener'); return true; },
  };

  function fmt(sec) { const m = Math.floor(sec / 60), s = Math.floor(sec % 60); return `${m}:${String(s).padStart(2, '0')}`; }
}

// Must come after the declarations above (module-level consts are in the TDZ until then).
if (flag('mock')) install();
else setTimeout(() => { if (!window.pywebview) install(); }, 300);
