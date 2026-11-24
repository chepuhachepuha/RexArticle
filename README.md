# RexArticles

Кроссплатформенное приложение для поиска и сбора файлов принтов по артикулам поставок WB и Ozon. Работает на Windows 10/11 x64 и macOS; на Apple Silicon используется Rosetta.

## Возможности

- загрузка поставок WB и Ozon из Trigu API или из локальных PDF;
- поиск принтов в локальных, сетевых и WebDAV-папках;
- SQLite-индексы с инкрементальным обновлением;
- ручные уточнения артикулов и файлов;
- системная светлая и тёмная тема.

Ключ Trigu и пользовательские настройки не добавляются в Git. Ключ вводится в приложении: **Настройки → API**.

## Локальный запуск

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt pyinstaller==6.16.0
python main.py
```

На Windows активируйте окружение командой `.venv\Scripts\activate`.

## Выпуск версии

Релизы создаются тегом формата `vX.Y.Z`. Его можно отправить с macOS или Windows:

```bash
git checkout main
git pull --ff-only
git tag -a v1.0.3 -m "RexArticles 1.0.3"
git push origin main --follow-tags
```

GitHub Actions проверит тесты, соберёт установщик Windows x64 и приложение macOS x64, а затем опубликует их в [GitHub Releases](https://github.com/chepuhachepuha/RexArticle/releases). Релиз содержит `RexArticles-Setup-vX.Y.Z.exe` и `RexArticles-macos-x64-vX.Y.Z.zip`.

Пакеты первого этапа не подписаны сертификатами Apple или Windows, поэтому система может запросить дополнительное подтверждение при первом запуске.
