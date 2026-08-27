# yt2tt — YouTube → вертикальные нарезки → TikTok

Конвейер: находит видео на YouTube (например, китайские дорамы), скачивает,
режет на короткие вертикальные части 1080×1920 и заливает их в аккаунт TikTok
через официальный Content Posting API — по порядку, с паузами и лимитом на сутки.

Состояние хранится в SQLite, поэтому запуск можно прерывать и повторять:
уже скачанное не скачивается заново, уже опубликованное не публикуется дважды.

> **Про контент.** Инструмент не привязан к конкретным видео: он работает с любым
> запросом, каналом или плейлистом. Заливка чужих сериалов нарушает авторские
> права и правила YouTube/TikTok — используйте его для своего контента или для
> того, на что у вас есть права. Ответственность за выбор источников на вас.

## Как это работает

```
search  ──▶  prepare  ─────────────────▶  upload
(YouTube    (yt-dlp: скачать           (TikTok API: init →
 Data API    ffmpeg: нарезать           чанки → poll статуса)
 или yt-dlp) на части 9:16)
     │            │                          │
     └────────────┴──── SQLite: videos / clips ──┘
```

1. **search** — ищет по запросам/каналам/плейлистам, отсекает по длительности и
   стоп-словам, кладёт новые видео в очередь (дубли отбрасываются по `video_id`).
2. **prepare** — скачивает через `yt-dlp`, считает раскладку частей и рендерит
   каждую часть в вертикальный mp4 (`ffmpeg`). Хвост короче `min_part_seconds`
   приклеивается к предыдущей части, а не выходит огрызком.
3. **upload** — постит части по очереди: `inbox` (черновик в приложении) или
   `direct` (сразу в ленту), с паузой `post_interval_seconds` и суточным лимитом.

## Установка

```bash
git clone https://github.com/Alivantiz/network.git && cd network
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"          # или: pip install -r requirements.txt
sudo apt install ffmpeg          # ffmpeg и ffprobe обязательны
```

## Быстрый старт

```bash
yt2tt init                       # создаст config.yaml и .env из шаблонов
$EDITOR config.yaml              # запросы, длина части, ориентация
$EDITOR .env                     # ключи (см. ниже)
yt2tt doctor                     # проверит ffmpeg, yt-dlp и ключи

yt2tt add "https://youtu.be/XXXX" --dry-run   # прогон на одном видео
yt2tt prepare --dry-run
yt2tt upload --dry-run           # покажет, что бы залилось, но не зальёт

yt2tt run                        # полный цикл: поиск → нарезка → заливка
yt2tt status                     # что в очереди и что уже опубликовано
```

## Ключи

**YouTube.** `YOUTUBE_API_KEY` не обязателен — без него поиск идёт через `yt-dlp`
(без квот, но метаданных чуть меньше). Ключ даёт точные фильтры и стабильный
поиск: [console.cloud.google.com](https://console.cloud.google.com/apis/library/youtube.googleapis.com).

**TikTok.** Нужно приложение в [TikTok for Developers](https://developers.tiktok.com/)
с включённым Content Posting API и скоупами `video.upload` (черновик) и/или
`video.publish` (прямая публикация). Токены получаются один раз:

```bash
yt2tt auth url --redirect-uri https://example.com/callback
# открыть ссылку, разрешить доступ, скопировать параметр code из редиректа
yt2tt auth exchange --code <code> --redirect-uri https://example.com/callback
# положить TIKTOK_REFRESH_TOKEN и TIKTOK_ACCESS_TOKEN в .env
yt2tt auth whoami                # проверка: инфо о креаторе и доступные privacy-опции
```

Access token обновляется автоматически по refresh token и кэшируется в
`work/.tiktok_token.json` (права 600).

Пока приложение не прошло аудит TikTok, посты видны только автору — поэтому
дефолт `mode: inbox` + `privacy_level: SELF_ONLY`. Для `mode: direct` нужно
показывать креатору его privacy-опции (`yt2tt auth whoami`) — этого требуют
правила API.

## Команды

| Команда | Что делает |
| --- | --- |
| `yt2tt init` | создать `config.yaml` и `.env` из шаблонов |
| `yt2tt doctor` | проверить внешние утилиты и ключи |
| `yt2tt search` | только поиск, пополнить очередь |
| `yt2tt add URL...` | добавить конкретные ссылки вручную |
| `yt2tt prepare [--limit N]` | скачать и нарезать |
| `yt2tt upload [--limit N]` | опубликовать готовые части |
| `yt2tt run [--skip-discovery]` | весь цикл целиком |
| `yt2tt status [--json]` | сводка по очереди |
| `yt2tt auth url\|exchange\|whoami` | OAuth-помощники TikTok |

Общие флаги: `-c config.yaml`, `--env-file .env`, `-v` (debug), `--dry-run`.

## Настройка

Всё в `config.yaml` (полный комментированный пример — `config.example.yaml`).
Основное:

| Ключ | Смысл |
| --- | --- |
| `search.queries` / `channels` / `playlists` | что искать и откуда брать |
| `search.min_duration_sec` / `max_duration_sec` | отсечь шорты и многочасовые склейки |
| `search.exclude_keywords` | стоп-слова в названии (трейлеры, реакции) |
| `clip.part_seconds` | длина одной части (по умолчанию 60 с) |
| `clip.min_part_seconds` | короткий хвост приклеить к предыдущей части |
| `clip.skip_intro_seconds` / `skip_outro_seconds` | срезать заставку и титры |
| `clip.max_parts` | сколько частей максимум с одного видео |
| `clip.orientation` | `blur` — размытый фон, `crop` — обрезка, `pad` — полосы, `none` |
| `metadata.title_template` | подпись: `{title} {index} {total} {channel} {hashtags}` |
| `tiktok.mode` | `inbox` (черновик) или `direct` (сразу пост) |
| `tiktok.post_interval_seconds` / `daily_limit` | темп публикаций |
| `runtime.keep_source` / `keep_clips` | что оставлять на диске |

Секреты в YAML не хранятся — только в окружении или `.env`.

## По расписанию

```cron
# каждый час: догрузить очередь и опубликовать одну порцию
0 * * * * cd /path/to/network && .venv/bin/yt2tt run --upload-limit 2 >> work/cron.log 2>&1
```

Параллельные запуски одного и того же конфига не нужны: состояние в SQLite,
но блокировки между процессами нет.

## Разработка

```bash
pip install -r requirements-dev.txt
pytest -q          # 61 тест, без сети и без ffmpeg (внешние вызовы застабены)
ruff check src tests
```

Структура:

```
src/yt2tt/
  cli.py          команды и аргументы
  config.py       YAML + .env, валидация
  youtube.py      поиск: Data API v3 и yt-dlp
  downloader.py   обёртка над yt-dlp
  video.py        раскладка частей (чистая логика) + ffmpeg
  metadata.py     подписи и хэштеги
  uploaders/      TikTok Content Posting API + dry-run
  pipeline.py     склейка стадий, лимиты, очистка
  state.py        SQLite: videos / clips
```

## Ограничения

- TikTok принимает видео до 10 минут; `part_seconds` больше 600 не имеет смысла.
- Прямая публикация (`direct`) доступна только приложениям, прошедшим аудит.
- YouTube Data API — квота 10 000 единиц в сутки; `search.list` стоит 100 за запрос.
- Нарезка перекодирует видео (libx264) — это упирается в CPU; для длинных
  видео уменьшайте `max_parts` или используйте `preset: ultrafast`.
