# PFDO Registry — документация проекта

> Последнее обновление: май 2026

## Что это

Внутренний веб-сервис для поиска данных учеников по ФИО.
Пользователь вводит ФИО и получает **номер сертификата**, **номер заявки** и **статус** (Активирован / Заморожен / Не активирован).
Данные других учеников недоступны и не отображаются.

Администратор загружает выгрузки из базы в формате Excel и следит за активностью через дашборд с логами.

---

## Стек

| Компонент | Технология |
|-----------|-----------|
| Язык | Python 3.14 |
| Фреймворк | FastAPI |
| База данных | SQLite (один файл) |
| Шаблоны | Jinja2 |
| Веб-сервер | nginx (reverse proxy) |
| Процесс | systemd (uvicorn) |
| ОС сервера | Ubuntu/Debian |
| Деплой | Git pull |

---

## Структура проекта

```
PFDO_Lipetsk/
└── registry/
    ├── main.py              # всё приложение (FastAPI)
    ├── registry.db          # база данных (создаётся автоматически, не в git)
    ├── requirements.txt     # зависимости
    ├── .env                 # секреты (не в git!)
    ├── uploads/             # загруженные Excel-файлы (не в git)
    ├── backups/             # автобэкапы базы (не в git)
    ├── static/              # CSS, JS (пока пусто)
    └── templates/
        ├── index.html       # страница пользователя
        ├── admin_login.html # вход в админку
        └── admin.html       # панель администратора
```

---

## Роли и доступ

### Пользователь (без авторизации)
- Открывает сайт, вводит ФИО
- Нашлась **1 запись** → видит номер сертификата, номер заявки, статус
- Нашлось **0 записей** → "Данные не найдены, обратитесь к администратору"
- Нашлось **2+ записей** → "Уточните ФИО — введите полностью"
- Ошибка сервера → "Произошла ошибка, обратитесь к администратору"
- ФИО найденной записи пользователю **не показывается**

### Администратор (логин + пароль)
- Загружает Excel-выгрузку, указывает названия столбцов
- Смотрит дашборд: статистика + журнал запросов (IP, время, запрос, результат)
- Видит список резервных копий базы

---

## База данных (SQLite)

### Таблица `records` — данные учеников
| Поле | Тип | Описание |
|------|-----|----------|
| id | INTEGER | первичный ключ |
| fio | TEXT | ФИО как в файле |
| fio_norm | TEXT | нормализованное ФИО для поиска |
| cert_number | TEXT | номер сертификата |
| app_number | TEXT | номер заявки |
| status | TEXT | Активирован / Заморожен / Не активирован |
| updated_at | TEXT | дата последнего обновления записи |

### Таблица `search_log` — логи запросов
| Поле | Тип | Описание |
|------|-----|----------|
| id | INTEGER | первичный ключ |
| ts | TEXT | timestamp (UTC) |
| ip | TEXT | IP-адрес пользователя |
| user_agent | TEXT | браузер / устройство |
| query | TEXT | что ввёл пользователь |
| result_type | TEXT | found / ambiguous / not_found |
| results_count | INTEGER | сколько записей найдено |
| found_fios | TEXT | какие ФИО были возвращены (только для админа) |

### Таблица `settings` — настройки
Хранит маппинг столбцов Excel и параметры последней загрузки.

### Таблица `admin_sessions` — сессии админа
Токены авторизации с временными метками. Сессия живёт 7 дней.

---

## API эндпоинты

| Метод | URL | Описание |
|-------|-----|----------|
| GET | `/` | страница пользователя |
| POST | `/search` | поиск по ФИО (JSON) |
| GET | `/admin/login` | форма входа |
| POST | `/admin/login` | аутентификация |
| GET | `/admin/logout` | выход |
| GET | `/admin` | панель администратора |
| POST | `/admin/upload` | загрузка Excel-файла |

---

## Безопасность

- SQL-инъекции: все запросы параметризованы через `?` — защищено ✅
- Импорт в транзакции: если файл битый — база не меняется ✅
- Автобэкап: перед каждой заменой базы создаётся копия в `backups/` ✅
- Хранятся последние 10 бэкапов, старые удаляются автоматически ✅
- `.env` не в git, пароль только в переменных окружения ✅
- Пользователь не видит ФИО других людей — сервер возвращает только 3 поля ✅
- HTTPS: настраивается через certbot после деплоя (см. ниже)
- Брутфорс: нет rate limit на логин — некритично при длинном пароле, можно добавить позже

---

## Формат Excel-выгрузки

Файл может иметь любые названия столбцов — при загрузке администратор указывает:
- какой столбец содержит ФИО
- какой — номер сертификата
- какой — номер заявки
- какой — статус (необязательно)

Поиск по названию столбца регистронезависимый. Поддерживаемые форматы: `.xlsx`, `.xls`

Статусы в базе: `Активирован`, `Заморожен`, `Не активирован`

---

## Установка на сервере (Ubuntu/Debian)

### 1. Клонировать репозиторий
```bash
cd /var/www
git clone https://github.com/NikolyaVOLCKOV/PFDO_Lipetsk.git
cd PFDO_Lipetsk/registry
```

### 2. Создать виртуальное окружение
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Создать файл .env
```bash
nano .env
```
Содержимое:
```
ADMIN_USER=admin
ADMIN_PASSWORD=ВАШ_НАДЁЖНЫЙ_ПАРОЛЬ
SECRET_KEY=сгенерировать_ниже
```
Сгенерировать SECRET_KEY:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### 4. Настроить systemd-сервис
```bash
sudo nano /etc/systemd/system/registry.service
```
Содержимое:
```ini
[Unit]
Description=PFDO Registry
After=network.target

[Service]
User=www-data
WorkingDirectory=/var/www/PFDO_Lipetsk/registry
EnvironmentFile=/var/www/PFDO_Lipetsk/registry/.env
ExecStart=/var/www/PFDO_Lipetsk/registry/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8001
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```
```bash
sudo systemctl daemon-reload
sudo systemctl enable registry
sudo systemctl start registry
sudo systemctl status registry
```

### 5. Настроить nginx
```bash
sudo nano /etc/nginx/sites-available/registry
```
Содержимое:
```nginx
server {
    listen 80;
    server_name registry.ВАШ_ДОМЕН.ru;

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    client_max_body_size 20M;
}
```
```bash
sudo ln -s /etc/nginx/sites-available/registry /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### 6. Настроить HTTPS (certbot)
```bash
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d registry.ВАШ_ДОМЕН.ru
```

---

## Деплой обновлений

```bash
cd /var/www/PFDO_Lipetsk
git pull
source registry/venv/bin/activate
pip install -r registry/requirements.txt
sudo systemctl restart registry
```

---

## TODO

- [x] Структура проекта и virtualenv
- [x] `requirements.txt`
- [x] `main.py` — FastAPI приложение
- [x] Шаблоны HTML (index, admin_login, admin)
- [x] Поиск с защитой от SQL-инъекций
- [x] Логирование запросов (IP, время, результат)
- [x] Транзакционный импорт Excel
- [x] Автобэкап базы
- [x] Дашборд с журналом запросов
- [x] Тест локально
- [ ] Настройка поддомена
- [ ] Деплой на сервер (git pull)
- [ ] Настройка systemd
- [ ] Настройка nginx
- [ ] HTTPS через certbot