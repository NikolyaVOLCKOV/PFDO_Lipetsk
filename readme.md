# PFDO Registry — документация проекта

> Последнее обновление: в процессе разработки

## Что это

Внутренний веб-сервис для поиска данных учеников по ФИО.  
Пользователь вводит ФИО и получает **номер сертификата**, **номер заявки** и **статус** (активен/нет).  
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
    ├── registry.db          # база данных (создаётся автоматически)
    ├── requirements.txt     # зависимости
    ├── .env                 # секреты (не в git!)
    ├── uploads/             # загруженные Excel-файлы
    ├── static/              # CSS, JS
    └── templates/
        ├── index.html       # страница пользователя
        ├── admin_login.html # вход в админку
        └── admin.html       # панель администратора
```

---

## Роли и доступ

### Пользователь (без авторизации)
- Открывает сайт
- Вводит ФИО (полностью или частично)
- Видит только: ФИО, номер сертификата, номер заявки, статус
- Больше ничего из базы не доступно

### Администратор (логин + пароль)
- Загружает Excel-выгрузку из базы
- Указывает какой столбец что означает
- Смотрит дашборд с логами запросов
- Может менять пароль и настройки

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
| status | TEXT | статус (активен / не активен) |
| updated_at | TEXT | дата последнего обновления записи |

### Таблица `search_log` — логи запросов
| Поле | Тип | Описание |
|------|-----|----------|
| id | INTEGER | первичный ключ |
| ts | TEXT | timestamp (UTC) |
| ip | TEXT | IP-адрес пользователя |
| user_agent | TEXT | браузер / устройство |
| query | TEXT | что ввёл пользователь |
| results_count | INTEGER | сколько записей найдено |
| found_fios | TEXT | какие ФИО были возвращены |

### Таблица `settings` — настройки
Хранит маппинг столбцов Excel и прочие параметры конфигурации.

### Таблица `admin_sessions` — сессии админа
Токены авторизации с временными метками.

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
| GET | `/admin/logs` | логи в JSON (для дашборда) |

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
ADMIN_PASSWORD=ВАШ_ПАРОЛЬ
SECRET_KEY=случайная_длинная_строка
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
```

### 5. Настроить nginx
```bash
sudo nano /etc/nginx/sites-available/registry
```
Содержимое (поддомен уточняется):
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

## Безопасность

- `.env` файл **не коммитится** в git (добавлен в `.gitignore`)
- `uploads/` и `registry.db` тоже **не в git**
- Пароль админа хранится только в `.env`, не в коде
- Все запросы пользователей логируются с IP и User-Agent
- Поиск возвращает только 3 поля — никаких лишних данных из БД

---

## Формат Excel-выгрузки

Файл может иметь любые названия столбцов — при загрузке администратор указывает:
- какой столбец содержит ФИО
- какой — номер сертификата
- какой — номер заявки
- какой — статус

Поддерживаемые форматы: `.xlsx`, `.xls`

---

## TODO / в разработке

- [ ] Структура проекта и virtualenv (шаг 1)
- [ ] `requirements.txt`
- [ ] `main.py` — FastAPI приложение
- [ ] Шаблоны HTML
- [ ] Тест локально
- [ ] Настройка nginx + поддомена
- [ ] Настройка systemd
- [ ] Деплой на сервер