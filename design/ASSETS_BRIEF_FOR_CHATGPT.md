# Бриф для ChatGPT (GPT Image): растровые ассеты BP Transcriber

Ты — арт-ассистент проекта **BP Transcriber**: десктопное приложение (macOS .dmg и Windows-установщик) для транскрибации русской речи с разделением по спикерам. Бренд: **Best Practice AI** (bestpracticeai.ru). Нужно сделать набор растровых картинок. Это бриф целиком: ничего больше читать не нужно.

Промпты ниже написаны по-английски намеренно: GPT Image лучше работает с английскими промптами. Тексты, которые должны оказаться на картинке, заданы дословно в кавычках. Остальное по-русски.

## 0. Куда сохранять и что вернуть

- Сохраняй **все файлы** в папку `/Users/ivansalin/Documents/GitHub/Transcriber/design/incoming/` (абсолютный путь, репозиторий пользователя).
- Имена файлов бери **ровно** из брифа (регистр, дефисы, `@2x`). Ничего не переименовывай.
- Если у тебя нет доступа к файловой системе пользователя: сохрани файлы у себя, дай ссылки на скачивание и перечисли имена, чтобы пользователь положил их в эту папку сам.
- В конце ответа выведи таблицу: имя файла, размер в пикселях, режим (RGBA или RGB), сохранён или нет, замечания.
- Не придумывай собственные логотипы Best Practice: официальный логотип («B∞ST PRACTICE» в золотом круге, буква «e» заменена шестерёнкой со знаком бесконечности) не использовать и не имитировать. Наш знак приложения: золотая шестерёнка, внутри неё аудио-волна (описание ниже).

## 1. Бренд и стиль (общий для всех ассетов)

**Палитра (только эти цвета и их оттенки):**

| Название | HEX | Роль |
|---|---|---|
| Dark Blue | `#0B1D3A` | основной фон, самые тёмные области |
| Steel Blue | `#1E3A5F` | вторичный фон, середина градиента |
| Light Steel | `#2A4F7A` | верх градиентов, подсветка фона |
| Gold | `#D4AF37` | главный акцент, линии, знак |
| Medium Gold | `#C4A032` | тени золота, нижняя часть градиента |
| Soft Gold | `#E8D48B` | блики, подсветка золота |
| Beige | `#F5DEB3` | вторичный текст на тёмном |
| Light BG | `#FAF9F6` | светлые фоны |

**Эстетика:** premium AI-engineering, «smart simplicity». Минимализм, чистая геометрия, много воздуха. Мотивы бренда: концентрические круги, шестиугольники (гексагоны), тонкие линии с узлами (как нейросеть или граф знаний), мягкое золотое свечение вокруг золотых элементов. Золото выглядит как матовый металл или тонкий градиент, не как жёлтая краска.

**Style lock (вставляй дословно в каждый промпт серии, он уже включён ниже):**
`Style lock: premium AI-engineering brand art, deep navy #0B1D3A to #1E3A5F gradients, gold #D4AF37 accents with soft #E8D48B highlights, thin precise lines, concentric circles and hexagon motifs with small glowing nodes, soft gold glow, flat clean vector-like rendering with subtle depth, generous negative space.`

**Звук и речь в образах:** концентрические звуковые волны, вертикальные штрихи аудио-волны (waveform bars), строки текста из тонких линий. Люди, лица, руки, микрофоны-«клипарт», наушники не нужны.

**Общий негативный список (добавляй к каждому промпту, уже включён):**
stock photo look, photorealistic people, faces, hands, clip-art, cartoon, 3D plastic, neon cyberpunk, purple or pink gradients, rainbow colors, glossy bubble effects, lens flare, heavy bokeh, noisy grain, watermark, signature, random letters, gibberish text, extra logos, existing brand logos, mockup frames, device frames.

## 2. Технические правила

- **Размеры.** GPT Image принимает стороны, кратные 16. Многие нужные размеры не кратны (например, 660×400, 164×314, 55×58, 1320×800). Поэтому: генерируй в ближайшем допустимом размере **с той же пропорцией или чуть шире**, затем доведи до точного размера через Python (Pillow): сначала `ImageOps.fit(img, (W, H), Image.LANCZOS)` (кроп по центру без растяжения), затем сохранение в PNG. Никогда не растягивай картинку непропорционально. Проверь итоговый размер кодом (`Image.open(path).size`), а не на глаз.
- **Прозрачность.** Там, где указан «transparent», генерируй с `background: transparent` (PNG, RGBA). Если интерфейс ChatGPT не позволяет прозрачный фон, сгенерируй на однородном фоне `#00FF00`, которого нет на самом объекте, и вырежь фон кодом с чистым альфа-краем (без зелёной каймы). Проверь **альфа-канал в самом файле** (`img.getchannel('A').getextrema()` должен дать `(0, 255)`), не по шахматке в превью.
- **Формат:** PNG, sRGB, без встроенных рамок и отступов, если не указано иначе. Для файлов без прозрачности режим RGB.
- **Текст на картинке только там, где он задан.** Если текст вышел с артефактами, регенерируй. Если после 3 попыток текст всё ещё кривой, сохрани версию **без текста** с суффиксом `-notext` (например `readme-hero-notext.png`) и сообщи об этом: текст добавим сами. Кириллицу проверяй побуквенно.
- Серии ассетов делай в **одном чате** (так стиль держится стабильнее) и повторяй style lock дословно.
- Если доступен выбор модели: для ассетов с текстом (d, e) и финальных файлов используй Sunburst/высокое качество, для черновиков быстрый вариант.

---

## Ассет (a): `app-icon-1024.png` — альтернативная концепция иконки

- **Назначение:** запасной вариант иконки приложения для macOS (сравнение с основной векторной иконкой, которую мы уже сделали).
- **Размер:** 1024×1024 px. **Формат:** PNG RGBA, **прозрачный фон**.
- **Safe zone:** сама иконка (скруглённый квадрат macOS, «squircle», радиус около 22,4 % стороны) занимает центральный квадрат **824×824 px**; поля по **100 px** со всех сторон остаются полностью прозрачными (в них допускается только мягкая тень под иконкой). Ключевой знак внутри не выходит за центральные 560×560 px.
- **Цвета:** фон иконки градиент `#2A4F7A` (верх) → `#1E3A5F` → `#0B1D3A` (низ); знак `#D4AF37` с бликом `#E8D48B`; свечение золотое, слабое.
- **Стиль-референс:** современная иконка macOS: объёмный, но сдержанный материал, тонкий светлый ободок по верхнему краю, мягкая внутренняя тень, ничего лишнего. Должна читаться в 16 px: форма знака крупная и жирная.
- **Текст:** нет.

**Промпт:**
```
Create a macOS app icon on a fully transparent background, 1024x1024 canvas. Scene: a single rounded-square "squircle" icon tile (continuous corner radius about 22% of its side) centered on the canvas, tile size 824x824 px, leaving a 100 px fully transparent margin on every side; only a very soft, subtle drop shadow may fall into the margin. Subject: the tile is a deep navy vertical gradient from #2A4F7A at the top through #1E3A5F to #0B1D3A at the bottom, with a thin soft light rim along the top edge. In the center, one bold gold emblem in #D4AF37 with a #E8D48B top highlight: a ten-tooth gear with a large round hole; inside the hole sits a short audio waveform of nine rounded vertical gold bars whose heights swell into two lobes with a tiny dot in the middle (the outline of the bars suggests an infinity sign). Important details: behind the emblem faint concentric circles and a faint hexagon with six small glowing nodes in #E8D48B at about 15 percent opacity, and a soft gold glow around the gear; shapes are bold and simple so the icon stays readable at 16 px. Use case: macOS Dock and Finder app icon. Constraints: isolated on a transparent background with clean alpha edges, no text, no letters, no logo, no mockup, no device frame, no background plate outside the tile, no stock-photo look, no neon, no purple, no extra objects. Style lock: premium AI-engineering brand art, deep navy #0B1D3A to #1E3A5F gradients, gold #D4AF37 accents with soft #E8D48B highlights, thin precise lines, concentric circles and hexagon motifs with small glowing nodes, soft gold glow, flat clean vector-like rendering with subtle depth, generous negative space.
```
**Негатив:** stock photo look, photorealistic, clip-art, cartoon, 3D plastic, neon, purple or pink, glossy bubbles, lens flare, text, letters, existing brand logos, device frames.

---

## Ассет (b): `dmg-background.png` и `dmg-background@2x.png` — фон окна установки .dmg

- **Назначение:** фон окна при открытии .dmg: пользователь перетаскивает иконку приложения в папку «Программы».
- **Размеры:** `dmg-background.png` = **660×400** px; `dmg-background@2x.png` = **1320×800** px (та же картинка, вдвое больше; делай @2x, затем уменьши до 1x с LANCZOS, чтобы композиция совпала).
- **Формат:** PNG RGB (**без прозрачности**), непрозрачный фон.
- **Safe zones (координаты в 1x, начало левый верх):** Finder положит иконку приложения с центром в **(165, 200)** и папку Applications с центром в **(495, 200)**. Вокруг каждой точки оставь **чистую круглую зону радиусом 90 px** (иконка 128 px плюс подпись снизу): никаких деталей, линий, ярких пятен, только плавный тёмный фон. Между зонами, на уровне y=200, между x≈262 и x≈398, нарисуй **стрелку-подсказку** слева направо: тонкая золотая линия с узлами или цепочка из 3–5 точек, заканчивающаяся аккуратной стрелкой (шеврон), без объёма. Верхние 40 px и нижние 40 px: только фоновый градиент (там строка заголовка Finder и отступ).
- **Цвета:** фон тёмный, градиент `#0B1D3A` → `#1E3A5F` по диагонали, слабая подсветка `#2A4F7A` в центре; стрелка и узоры `#D4AF37` / `#E8D48B`. Подписи иконок в Finder будут белыми, поэтому фон тёмный и ровный под ними.
- **Стиль-референс:** тихий премиальный фон, узор только по периферии: огромные концентрические окружности из тонких линий, уходящие за края, едва заметная сетка из шестиугольников, 5–7 крошечных светящихся узлов. Контраст узора низкий (линии на 10–20 % яркости), чтобы не спорить с иконками.
- **Текст:** нет.

**Промпт (генерируй в 1536×1024, затем fit/crop до 1320×800 и уменьши до 660×400):**
```
Create a quiet premium installer window background, wide 33:20 landscape composition. Scene: a deep navy canvas with a smooth diagonal gradient from #0B1D3A to #1E3A5F and a very soft #2A4F7A glow in the middle. Subject: large concentric circles drawn as hairline gold lines that sweep off the left and right edges, plus a barely visible hexagon grid fading out toward the center, and five or six tiny glowing nodes in #E8D48B on the periphery. In the exact middle of the canvas, on the horizontal centerline between 40 percent and 60 percent of the width, a thin elegant gold #D4AF37 arrow made of a fine line with two small nodes, pointing from left to right, flat and minimal. Important details: two completely clean, empty, perfectly smooth dark circular zones, each with a radius of about 22 percent of the image height, centered at 25 percent and 75 percent of the width on the horizontal centerline, with no lines, no glow and no pattern inside them; the top and bottom 10 percent of the image contain only the plain gradient. Use case: macOS disk image (.dmg) window background where two app icons will be placed over the clean zones. Constraints: no text, no letters, no logos, no icons, no folders, no people, no 3D objects, low contrast pattern only at the periphery. Style lock: premium AI-engineering brand art, deep navy #0B1D3A to #1E3A5F gradients, gold #D4AF37 accents with soft #E8D48B highlights, thin precise lines, concentric circles and hexagon motifs with small glowing nodes, soft gold glow, flat clean vector-like rendering with subtle depth, generous negative space.
```
**Негатив:** text, letters, logos, app icons, folders, bright patterns near the two clean zones, busy textures, photo, 3D, neon, purple.

---

## Ассет (c): `empty-state.png` — иллюстрация для зоны перетаскивания

- **Назначение:** пустое состояние главного окна («перетащите аудиофайл»), рисуется по центру зоны drop.
- **Размер:** **800×600** px. **Формат:** PNG RGBA, **прозрачный фон**.
- **Safe zone:** композиция по центру, внутри прямоугольника 640×480 px; поля 80 px по горизонтали и 60 px по вертикали прозрачные. Иллюстрация должна нормально смотреться и на тёмном (`#0B1D3A`), и на светлом (`#FAF9F6`) фоне приложения: используй только золото (`#D4AF37`, `#C4A032`, `#E8D48B`), без тёмных заливок и без белого.
- **Идея:** слева концентрические звуковые волны (дуги из тонких золотых линий, расходящиеся из точки), правее они превращаются в строки текста: вертикальные штрихи волны постепенно становятся горизонтальными линиями-строками разной длины (как абзац). Речь превращается в текст. Одна-две «ноды» с мягким свечением.
- **Текст:** нет (строки текста изображены линиями, не буквами).

**Промпт:**
```
Create a minimal line illustration on a fully transparent background, 800x600 canvas, content centered inside a 640x480 area. Scene: on the left, a small gold point emits concentric sound-wave arcs of hairline gold strokes that grow outward; in the middle the arcs break up into short vertical audio-waveform bars of varying height; on the right the bars transform into five horizontal lines of different lengths that read as lines of a text paragraph. Subject: only gold line work in #D4AF37 with a few #E8D48B highlights and #C4A032 shadows, two small glowing nodes, a very soft gold glow around the point. Important details: strokes are thin and precise, rounded caps, even spacing, the whole flow reads left to right as speech turning into text; it must look good on both a dark navy and a near-white background, so no dark fills and no white elements. Use case: empty-state illustration in a desktop app drop zone. Constraints: isolated on a transparent background with clean alpha edges, no drop shadow, no background plate, no letters or real words (text lines are abstract strokes), no microphone, no people, no frame. Style lock: premium AI-engineering brand art, deep navy #0B1D3A to #1E3A5F gradients, gold #D4AF37 accents with soft #E8D48B highlights, thin precise lines, concentric circles and hexagon motifs with small glowing nodes, soft gold glow, flat clean vector-like rendering with subtle depth, generous negative space.
```
**Негатив:** background plate, drop shadow, letters, words, microphone, headphones, people, dark fills, white elements, photo, 3D.

---

## Ассет (d): `readme-hero.png` — баннер для README

- **Назначение:** шапка README репозитория на GitHub.
- **Размер:** **1600×600** px. **Формат:** PNG RGB, непрозрачный.
- **Safe zone:** текст и знак внутри прямоугольника с полями **80 px** слева/справа и **60 px** сверху/снизу. Текстовый блок слева, по вертикали по центру; декоративная часть справа (примерно 40 % ширины).
- **Текст (точно, ничего не добавлять и не менять):**
  - Заголовок: `BP Transcriber` (латиница, Montserrat Bold или максимально близкая геометрическая гротескная гарнитура, цвет `#FFFFFF`, высота заглавных около 86 px).
  - Подзаголовок: `Транскрибатор русской речи · GigaAM` (кириллица, Montserrat Regular, цвет `#F5DEB3`, высота заглавных около 34 px; разделитель `·` это средняя точка U+00B7).
  - Над текстом слева можно поставить маленький золотой знак шестерёнки с волной (без надписей).
- **Фон:** градиент `#0B1D3A` → `#1E3A5F`, справа золотая композиция: шестерёнка с аудио-волной в центре, вокруг концентрические окружности, гексагональная сетка и узлы, свечение.

**Промпт:**
```
Create a wide premium README hero banner, 8:3 landscape (1600x600). Scene: a deep navy background with a smooth horizontal gradient from #0B1D3A on the left to #1E3A5F on the right. Subject: on the left half, a two-line text block, vertically centered, with the title exactly "BP Transcriber" in white #FFFFFF, bold geometric sans-serif (Montserrat Bold style), large, and below it the subtitle exactly "Транскрибатор русской речи · GigaAM" in beige #F5DEB3, regular geometric sans-serif (Montserrat Regular style), about 40 percent of the title size. On the right 40 percent of the banner, a bold gold #D4AF37 gear with ten teeth and a round hole containing a short audio waveform of nine rounded vertical bars swelling into two lobes, surrounded by thin concentric circles, a faint hexagon and a few small glowing #E8D48B nodes, with a soft gold glow. Important details: keep an 80 px margin left and right and 60 px top and bottom free of any text; the text must be crisp, correctly spelled, with correct Cyrillic letters; the left half behind the text stays calm and uncluttered for legibility. Use case: GitHub README header image for a desktop speech transcription app. Constraints: render ONLY these two text strings and no other words, no extra letters, no pseudo-text, no logos of other brands, no people, no photo. EXACT TEXT line 1: "BP Transcriber". EXACT TEXT line 2: "Транскрибатор русской речи · GigaAM". Style lock: premium AI-engineering brand art, deep navy #0B1D3A to #1E3A5F gradients, gold #D4AF37 accents with soft #E8D48B highlights, thin precise lines, concentric circles and hexagon motifs with small glowing nodes, soft gold glow, flat clean vector-like rendering with subtle depth, generous negative space.
```
**Негатив:** misspelled text, extra words, gibberish letters, Latin letters replacing Cyrillic, stock photo, people, other logos, busy background behind text, neon, purple.

*Рекомендация:* генерируй в 1536×1024 с кадром 8:3 по центру или в 1600×608, затем кроп до 1600×600.

---

## Ассет (e): `github-social-preview.png` — превью для соцсетей GitHub

- **Назначение:** Settings → Social preview репозитория (картинка при шеринге ссылки).
- **Размер:** **1280×640** px. **Формат:** PNG RGB, непрозрачный (вес до 1 МБ).
- **Safe zone:** **40 px** со всех сторон полностью свободны от текста и важных элементов (GitHub и соцсети режут края). Всё важное в области 1200×560.
- **Текст (точно):**
  - Заголовок: `BP Transcriber` (белый `#FFFFFF`, Montserrat Bold-подобный).
  - Подзаголовок: `Транскрибатор русской речи · GigaAM` (бежевый `#F5DEB3`, Montserrat Regular-подобный).
  - Маленькая строка внизу слева (опционально, мелко, `#E8D48B`): `bestpracticeai.ru`.
- **Композиция:** центрированная или с текстом слева и знаком справа; знак: золотая шестерёнка с аудио-волной, концентрические круги, гексагоны, узлы, свечение. Чуть более «крупный» и контрастный, чем hero, потому что превью смотрят в миниатюре.

**Промпт:**
```
Create a social preview card, 2:1 landscape (1280x640). Scene: deep navy gradient background from #0B1D3A to #1E3A5F with a soft #2A4F7A glow behind the emblem. Subject: on the left, a text block vertically centered with the title exactly "BP Transcriber" in white #FFFFFF, bold geometric sans-serif (Montserrat Bold style), very large, and under it the subtitle exactly "Транскрибатор русской речи · GigaAM" in beige #F5DEB3, regular geometric sans-serif; at the bottom left, tiny, the exact string "bestpracticeai.ru" in soft gold #E8D48B. On the right, a large bold gold #D4AF37 ten-tooth gear with a round hole holding a short audio waveform of nine rounded vertical bars that swell into two lobes, framed by thin concentric circles, a faint hexagon and small glowing nodes, with a soft gold glow. Important details: keep a 40 px margin on all four sides completely free of text and key elements; text crisp, correctly spelled, correct Cyrillic; strong contrast so it reads at thumbnail size. Use case: GitHub repository social preview image. Constraints: render ONLY these three text strings, no other words or pseudo-text, no people, no photo, no other brand logos. EXACT TEXT line 1: "BP Transcriber". EXACT TEXT line 2: "Транскрибатор русской речи · GigaAM". EXACT TEXT line 3: "bestpracticeai.ru". Style lock: premium AI-engineering brand art, deep navy #0B1D3A to #1E3A5F gradients, gold #D4AF37 accents with soft #E8D48B highlights, thin precise lines, concentric circles and hexagon motifs with small glowing nodes, soft gold glow, flat clean vector-like rendering with subtle depth, generous negative space.
```
**Негатив:** misspelled text, extra words, gibberish letters, text touching the edges, stock photo, people, other logos, neon, purple.

---

## Ассет (f): картинки для установщика Windows (Inno Setup)

Inno Setup принимает BMP; мы конвертируем PNG в BMP сами. Нужны **PNG-исходники без альфа-канала** (непрозрачные, режим RGB).

### f1. `installer-wizard.png` и `installer-wizard@2x.png` — боковая панель мастера

- **Размеры:** `installer-wizard.png` = **164×314** px, `installer-wizard@2x.png` = **328×628** px (вертикальный формат 0,522).
- **Формат:** PNG RGB, непрозрачный.
- **Safe zone:** поля 12 px (24 px в @2x). Знак в верхней трети (центр на высоте около 30 %), нижняя треть почти пустая, только тонкий узор и градиент (там Inno может накладывать системные элементы).
- **Композиция:** вертикальный градиент `#2A4F7A` (верх) → `#1E3A5F` → `#0B1D3A` (низ). Золотой знак (шестерёнка с аудио-волной) диаметром около 110 px (220 px в @2x), под ним тонкие концентрические дуги, уходящие за края, цепочка узлов, внизу едва заметные гексагоны.
- **Текст:** нет.

**Промпт (генерируй в 656×1248 или близком кратном 16, затем fit до 328×628 и уменьши до 164×314):**
```
Create a tall vertical installer side panel, aspect ratio 164:314. Scene: smooth vertical gradient from #2A4F7A at the top through #1E3A5F to #0B1D3A at the bottom. Subject: in the upper third, centered horizontally, a bold gold #D4AF37 ten-tooth gear (soft #E8D48B top highlight) with a round hole containing a short audio waveform of nine rounded vertical bars swelling into two lobes, about 65 percent of the panel width, with a soft gold glow; below it thin gold concentric arcs that sweep off the left and right edges, a short vertical chain of tiny glowing nodes connected by a hairline, and in the bottom third only very faint hexagon outlines at low contrast. Important details: keep a 7 percent margin on every side; the bottom third is calm and almost empty; strictly opaque, no transparency. Use case: Windows installer wizard left banner. Constraints: no text, no letters, no logos, no people, no photo, no 3D plastic, no neon, no purple. Style lock: premium AI-engineering brand art, deep navy #0B1D3A to #1E3A5F gradients, gold #D4AF37 accents with soft #E8D48B highlights, thin precise lines, concentric circles and hexagon motifs with small glowing nodes, soft gold glow, flat clean vector-like rendering with subtle depth, generous negative space.
```
**Негатив:** text, letters, logos, transparency, busy details in the bottom third, photo, 3D, neon, purple.

### f2. `installer-small.png` и `installer-small@2x.png` — малая иконка в шапке мастера

- **Размеры:** `installer-small.png` = **55×58** px, `installer-small@2x.png` = **110×116** px.
- **Формат:** PNG RGB, **непрозрачный** белый фон `#FFFFFF` (шапка мастера Inno светлая; BMP не хранит альфу). Скруглённая иконка (squircle) или просто знак, по центру, поля 3 px (6 px в @2x).
- **Содержимое:** тот же знак: скруглённая плитка с градиентом `#1E3A5F` → `#0B1D3A` и золотая шестерёнка с аудио-волной; максимально простые, жирные формы, читаемые в 55 px. Узор на фоне плитки не нужен.
- **Текст:** нет.

**Промпт (генерируй 1024×1024 на белом фоне, затем fit с полями до пропорции 55:58 и уменьши):**
```
Create a tiny-icon-ready emblem on a pure white #FFFFFF background, square canvas. Scene: a single rounded-square tile with a navy vertical gradient from #1E3A5F to #0B1D3A, centered, filling about 90 percent of the canvas width. Subject: on the tile, one bold gold #D4AF37 ten-tooth gear with a thick body and a large round hole, inside the hole a short waveform of seven chunky rounded vertical gold bars with a small dot in the middle. Important details: extremely simple, bold shapes that remain readable at 55 px, no fine lines, no pattern, no glow, flat with a very subtle top highlight. Use case: small header icon of a Windows installer, converted to BMP on white. Constraints: pure white background, no transparency, no shadow, no text, no letters, no logos, no extra objects. Style lock: premium AI-engineering brand art, deep navy #0B1D3A to #1E3A5F gradients, gold #D4AF37 accents with soft #E8D48B highlights, flat clean vector-like rendering with subtle depth, generous negative space.
```
**Негатив:** fine lines, small details, glow, drop shadow, text, transparency, colored background, photo.

---

## Ассет (g, опционально): `about-illustration.png` — иллюстрация для окна «О программе»

- **Назначение:** иллюстрация вверху диалога «О программе» / на экране онбординга.
- **Размер:** **1200×800** px. **Формат:** PNG RGBA, **прозрачный фон** (золото и мягкие тона, чтобы работало и на светлой, и на тёмной теме приложения).
- **Safe zone:** содержимое в центральной области 1000×640 px, поля 100 px и 80 px прозрачные.
- **Идея:** изометрично-плоская схема процесса: слева аудио-волна, в центре шестерёнка (движок распознавания), справа три строки текста с цветными (золотой, мягко-золотой, бежевый) «метками спикеров» в виде небольших кружков-узлов (идея разделения по спикерам). Всё соединено тонкими линиями с узлами, вокруг лёгкие концентрические окружности и гексагоны.
- **Текст:** нет.

**Промпт:**
```
Create a clean process illustration on a fully transparent background, 3:2 landscape (1200x800), content inside the central 1000x640 area. Scene: a horizontal flow from left to right connected by a hairline gold path with small nodes. Subject: on the left, a short audio waveform of rounded vertical bars in gold #D4AF37; in the center, a bold ten-tooth gear in gold #D4AF37 with soft #E8D48B highlight and a soft gold glow, surrounded by thin concentric circles and a faint hexagon; on the right, three stacked paragraphs of text drawn as abstract horizontal lines of different lengths, each paragraph starting with a small round speaker marker in a different tone (#D4AF37, #E8D48B, #F5DEB3). Important details: only gold and warm light tones, no dark fills and no white, so it works on both dark navy and near-white backgrounds; flat clean vector-like rendering with a little depth; plenty of negative space. Use case: About dialog and onboarding illustration of a speech-to-text app with speaker separation. Constraints: isolated on a transparent background with clean alpha edges, no drop shadow, no background plate, no letters or real words, no people, no microphone, no photo. Style lock: premium AI-engineering brand art, deep navy #0B1D3A to #1E3A5F gradients, gold #D4AF37 accents with soft #E8D48B highlights, thin precise lines, concentric circles and hexagon motifs with small glowing nodes, soft gold glow, flat clean vector-like rendering with subtle depth, generous negative space.
```
**Негатив:** background plate, drop shadow, letters, words, people, microphone, dark fills, white elements, photo, 3D, neon, purple.

---

## Сводная таблица файлов

| Файл | Размер, px | Режим | Фон |
|---|---|---|---|
| `app-icon-1024.png` | 1024×1024 | RGBA | прозрачный (иконка 824×824, поля 100) |
| `dmg-background.png` | 660×400 | RGB | непрозрачный |
| `dmg-background@2x.png` | 1320×800 | RGB | непрозрачный |
| `empty-state.png` | 800×600 | RGBA | прозрачный |
| `readme-hero.png` | 1600×600 | RGB | непрозрачный |
| `github-social-preview.png` | 1280×640 | RGB | непрозрачный |
| `installer-wizard.png` | 164×314 | RGB | непрозрачный |
| `installer-wizard@2x.png` | 328×628 | RGB | непрозрачный |
| `installer-small.png` | 55×58 | RGB | белый |
| `installer-small@2x.png` | 110×116 | RGB | белый |
| `about-illustration.png` (опц.) | 1200×800 | RGBA | прозрачный |

Папка сохранения: `/Users/ivansalin/Documents/GitHub/Transcriber/design/incoming/`

## Чек-лист «перед сохранением проверь…»

1. **Размер:** точные пиксели из таблицы выше проверены кодом (`Image.open(...).size`), а не на глаз; @2x ровно вдвое больше 1x.
2. **Прозрачность:** у файлов RGBA альфа-канал реально содержит прозрачность (`getextrema() == (0, 255)`), края чистые, нет зелёной или белой каймы; у файлов RGB нет альфа-канала.
3. **Нет артефактов в тексте:** на `readme-hero.png` и `github-social-preview.png` есть только заданные строки; нет лишних слов, «кракозябр», двойных букв.
4. **Кириллица:** строка `Транскрибатор русской речи · GigaAM` совпадает побуквенно (не `Tpaнскрибатор`, не латинские «р», «а», «о» вместо кириллических; средняя точка на месте).
5. **Safe zones:** в `app-icon-1024.png` поля 100 px пустые; в `dmg-background*.png` чисто вокруг точек (165,200) и (495,200) в 1x; в соцпревью поля 40 px свободны.
6. **Палитра:** только бренд-цвета, нет фиолетового, розового, неона; золото матовое, не жёлтое.
7. **Нет чужих логотипов, лиц и людей;** нет попытки воспроизвести официальный логотип «B∞ST PRACTICE».
8. **Имена файлов** ровно как в таблице, все лежат в `/Users/ivansalin/Documents/GitHub/Transcriber/design/incoming/`.
9. **Отчёт:** выведи таблицу (имя, размер, режим, статус, замечания) и отдельно перечисли всё, что пришлось сохранить в версии `-notext` или сделать с компромиссом.
