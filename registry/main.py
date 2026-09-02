import os, sqlite3, secrets, io, csv, re, shutil
from pathlib import Path
from functools import wraps
from datetime import datetime

from fastapi import FastAPI, Request, Form, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv
import openpyxl

# ── конфиг ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")

DB_PATH     = BASE_DIR / "registry.db"
BACKUP_DIR  = BASE_DIR / "backups"
UPLOAD_DIR  = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
BACKUP_DIR.mkdir(exist_ok=True)

ADMIN_USER     = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "changeme123")
SECRET_KEY     = os.environ.get("SECRET_KEY", secrets.token_hex(32))

# ── приложение ────────────────────────────────────────────────────────────────
app = FastAPI()
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

# ── БД ────────────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS records (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                fio         TEXT NOT NULL,
                fio_norm    TEXT NOT NULL,
                cert_number TEXT,
                app_number  TEXT,
                birth_date  TEXT,
                status      TEXT DEFAULT 'Не активирован',
                updated_at  TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_fio_norm ON records(fio_norm);

            CREATE TABLE IF NOT EXISTS search_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                ts            TEXT NOT NULL,
                ip            TEXT,
                user_agent    TEXT,
                query         TEXT,
                result_type   TEXT,
                results_count INTEGER DEFAULT 0,
                found_fios    TEXT
            );

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS admin_sessions (
                token      TEXT PRIMARY KEY,
                created_at TEXT
            );
        """)
        # миграция для баз, созданных до появления поля "дата рождения"
        cols = [r["name"] for r in db.execute("PRAGMA table_info(records)").fetchall()]
        if "birth_date" not in cols:
            db.execute("ALTER TABLE records ADD COLUMN birth_date TEXT")

init_db()

# ── бэкап ─────────────────────────────────────────────────────────────────────
def backup_db():
    """Копирует базу перед каждой заменой. Хранит последние 10 копий."""
    if not DB_PATH.exists():
        return
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    dst = BACKUP_DIR / f"registry_{ts}.db"
    shutil.copy2(DB_PATH, dst)
    # удаляем старые бэкапы, оставляем 10 последних
    backups = sorted(BACKUP_DIR.glob("registry_*.db"))
    for old in backups[:-10]:
        old.unlink()

# ── утилиты ───────────────────────────────────────────────────────────────────
def normalize(s: str) -> str:
    return " ".join(str(s).lower().split())

def digits_only(s: str) -> str:
    """Оставляет только цифры — чтобы сравнивать дату рождения
    независимо от разделителей (01.02.2010 / 01-02-2010 / 01 02 2010)."""
    return re.sub(r"\D", "", s or "")

def status_style(status: str) -> str:
    s = (status or "").strip().lower()
    if "отозв" in s:
        return "revoked"
    # отрицание — в первую очередь: "Не активирован", "Не подтверждена"
    if s.startswith("не "):
        return "inactive"
    if "заморо" in s:
        return "frozen"
    if "актив" in s or "подтвержд" in s:
        return "active"
    return "inactive"

def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

def get_session_token(request: Request):
    return request.cookies.get("admin_token")

def is_admin(request: Request) -> bool:
    token = get_session_token(request)
    if not token:
        return False
    with get_db() as db:
        row = db.execute(
            "SELECT 1 FROM admin_sessions WHERE token = ?", (token,)
        ).fetchone()
    return row is not None

def admin_required(func):
    @wraps(func)
    async def wrapper(request: Request, *args, **kwargs):
        if not is_admin(request):
            return RedirectResponse("/admin/login", status_code=302)
        return await func(request, *args, **kwargs)
    return wrapper

def get_setting(key: str, default: str = "") -> str:
    with get_db() as db:
        row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default

def set_setting(key: str, value: str):
    with get_db() as db:
        db.execute(
            "INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value)
        )

# ── публичная страница ────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/search", response_class=JSONResponse)
async def search(request: Request, fio: str = Form(""), birth: str = Form("")):
    fio   = fio.strip()
    birth = birth.strip()
    if len(fio) < 2:
        return JSONResponse({"error": "Введите не менее 2 символов"}, status_code=400)

    query = normalize(fio)
    ip    = get_client_ip(request)
    ua    = request.headers.get("User-Agent", "")[:300]
    ts    = datetime.utcnow().isoformat(sep=" ", timespec="seconds")

    try:
        with get_db() as db:
            rows = db.execute(
                "SELECT fio, cert_number, app_number, birth_date, status "
                "FROM records WHERE fio_norm LIKE ?",
                (f"%{query}%",)
            ).fetchall()
    except Exception:
        return JSONResponse({"error": "server_error"}, status_code=500)

    # если указана дата рождения — сужаем совпадения по ней
    # (сравниваем только цифры, не завязываясь на формат разделителей)
    birth_digits = digits_only(birth)
    if birth_digits:
        rows = [r for r in rows if digits_only(r["birth_date"]) == birth_digits]

    count = len(rows)

    if count == 0:
        result_type = "not_found"
    elif count == 1:
        result_type = "found"
    else:
        result_type = "ambiguous"

    found_fios = [r["fio"] for r in rows]
    log_query  = f"{fio} | ДР: {birth}" if birth else fio

    with get_db() as db:
        db.execute(
            "INSERT INTO search_log"
            "(ts, ip, user_agent, query, result_type, results_count, found_fios) "
            "VALUES(?,?,?,?,?,?,?)",
            (ts, ip, ua, log_query, result_type, count, "; ".join(found_fios) or None)
        )

    if count == 0:
        return JSONResponse({"type": "not_found"})

    if count > 1:
        return JSONResponse({"type": "ambiguous", "count": count})

    r = rows[0]
    return JSONResponse({
        "type":         "found",
        "cert":         r["cert_number"] or "—",
        "app":          r["app_number"]  or "—",
        "birth":        r["birth_date"]  or "—",
        "status":       r["status"]      or "Не активирован",
        "status_style": status_style(r["status"] or ""),
    })

# ── авторизация админа ────────────────────────────────────────────────────────
@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(request: Request):
    if is_admin(request):
        return RedirectResponse("/admin", status_code=302)
    return templates.TemplateResponse("admin_login.html", {"request": request, "error": None})

@app.post("/admin/login", response_class=HTMLResponse)
async def admin_login(
    request:  Request,
    username: str = Form(""),
    password: str = Form("")
):
    if username == ADMIN_USER and password == ADMIN_PASSWORD:
        token = secrets.token_hex(32)
        with get_db() as db:
            db.execute(
                "INSERT INTO admin_sessions(token, created_at) VALUES(?,?)",
                (token, datetime.utcnow().isoformat())
            )
        resp = RedirectResponse("/admin", status_code=302)
        resp.set_cookie("admin_token", token, httponly=True, samesite="lax", max_age=86400 * 7)
        return resp
    return templates.TemplateResponse(
        "admin_login.html",
        {"request": request, "error": "Неверный логин или пароль"}
    )

@app.get("/admin/logout")
async def admin_logout(request: Request):
    token = get_session_token(request)
    if token:
        with get_db() as db:
            db.execute("DELETE FROM admin_sessions WHERE token=?", (token,))
    resp = RedirectResponse("/admin/login", status_code=302)
    resp.delete_cookie("admin_token")
    return resp

# ── админ-панель ──────────────────────────────────────────────────────────────
@app.get("/admin", response_class=HTMLResponse)
@admin_required
async def admin_panel(request: Request):
    with get_db() as db:
        total  = db.execute("SELECT COUNT(*) as n FROM records").fetchone()["n"]
        active = db.execute(
            "SELECT COUNT(*) as n FROM records WHERE status = 'Активирован'"
        ).fetchone()["n"]
        logs = db.execute(
            "SELECT ts, ip, user_agent, query, result_type, results_count, found_fios "
            "FROM search_log ORDER BY id DESC LIMIT 200"
        ).fetchall()
        today = datetime.utcnow().strftime("%Y-%m-%d")
        today_count = db.execute(
            "SELECT COUNT(*) as n FROM search_log WHERE ts LIKE ?",
            (f"{today}%",)
        ).fetchone()["n"]
        not_found_count = db.execute(
            "SELECT COUNT(*) as n FROM search_log WHERE result_type = 'not_found'"
        ).fetchone()["n"]
        ambiguous_count = db.execute(
            "SELECT COUNT(*) as n FROM search_log WHERE result_type = 'ambiguous'"
        ).fetchone()["n"]

    # список бэкапов
    backups = sorted(BACKUP_DIR.glob("registry_*.db"), reverse=True)
    backup_list = [
        {"name": b.name, "size": f"{b.stat().st_size // 1024} КБ"}
        for b in backups[:10]
    ]

    return templates.TemplateResponse("admin.html", {
        "request":         request,
        "total":           total,
        "active":          active,
        "today_count":     today_count,
        "not_found_count": not_found_count,
        "ambiguous_count": ambiguous_count,
        "logs":            [dict(r) for r in logs],
        "last_up":         get_setting("last_upload",   "—"),
        "last_fn":         get_setting("last_filename", "—"),
        "col_fio":         get_setting("col_fio",  ""),
        "col_cert":        get_setting("col_cert", ""),
        "col_app":         get_setting("col_app",  ""),
        "col_birth":       get_setting("col_birth", ""),
        "col_stat":        get_setting("col_stat", ""),
        "backup_list":     backup_list,
    })

# ── разбор загруженного файла (xlsx или csv) ───────────────────────────────────
def parse_xlsx_rows(content: bytes):
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    return list(ws.iter_rows(values_only=True))

def parse_csv_rows(content: bytes):
    # пробуем несколько кодировок — экспорт из 1С/Excel часто в cp1251
    text = None
    for enc in ("utf-8-sig", "cp1251", "utf-8"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = content.decode("utf-8", errors="replace")

    # определяем разделитель (запятая или точка с запятой — частый случай в RU-локали)
    sample = text[:2048]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=";,\t").delimiter
    except csv.Error:
        delimiter = ";" if sample.count(";") > sample.count(",") else ","

    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    return [tuple(row) for row in reader]

def parse_upload_rows(content: bytes, filename: str):
    ext = Path(filename or "").suffix.lower()
    if ext == ".csv":
        return parse_csv_rows(content)
    return parse_xlsx_rows(content)  # .xlsx / .xls / неизвестное — пробуем как Excel

# ── загрузка Excel/CSV ──────────────────────────────────────────────────────────
@app.post("/admin/upload", response_class=HTMLResponse)
@admin_required
async def admin_upload(
    request:   Request,
    file:      UploadFile = File(...),
    col_fio:   str = Form(""),
    col_cert:  str = Form(""),
    col_app:   str = Form(""),
    col_birth: str = Form(""),
    col_stat:  str = Form(""),
    replace:   str = Form("yes"),
):
    content = await file.read()
    try:
        rows = parse_upload_rows(content, file.filename)
    except Exception:
        return RedirectResponse("/admin?error=read_error", status_code=302)

    if not rows:
        return RedirectResponse("/admin?error=empty", status_code=302)

    header = [str(c).strip() if c is not None else "" for c in rows[0]]

    def col_index(name: str):
        name = name.strip()
        if not name:
            return None
        if name.isdigit():
            idx = int(name)
            return idx if 0 <= idx < len(header) else None
        try:
            return header.index(name)
        except ValueError:
            pass
        name_l = name.lower()
        for i, h in enumerate(header):
            if h.lower() == name_l:
                return i
        return None

    idx_fio   = col_index(col_fio)
    idx_cert  = col_index(col_cert)
    idx_app   = col_index(col_app)
    idx_birth = col_index(col_birth)
    idx_stat  = col_index(col_stat)

    if idx_fio is None:
        return RedirectResponse("/admin?error=col_fio_not_found", status_code=302)

    def cell(row, idx):
        if idx is None or idx >= len(row):
            return ""
        v = row[idx]
        v = str(v).strip() if v is not None else ""
        # снять обёртку вида ="..." — артефакт Excel/CSV,
        # которым сохраняют текст с ведущими нулями (например номера сертификатов)
        m = re.match(r'^="(.*)"$', v)
        if m:
            v = m.group(1)
        return v

    # ── собираем записи из файла ДО изменения базы ──
    to_insert = []
    for row in rows[1:]:
        fio = cell(row, idx_fio)
        if not fio or fio.lower() == "none":
            continue
        to_insert.append((
            fio,
            normalize(fio),
            cell(row, idx_cert),
            cell(row, idx_app),
            cell(row, idx_birth),
            cell(row, idx_stat) or "Не активирован",
            datetime.utcnow().isoformat(sep=" ", timespec="seconds"),
        ))

    if not to_insert:
        return RedirectResponse("/admin?error=empty", status_code=302)

    # ── бэкап + запись в транзакции ──
    if replace == "yes":
        backup_db()

    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute("BEGIN")
        if replace == "yes":
            conn.execute("DELETE FROM records")
        conn.executemany(
            "INSERT INTO records"
            "(fio, fio_norm, cert_number, app_number, birth_date, status, updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            to_insert
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        conn.close()
        return RedirectResponse("/admin?error=import_failed", status_code=302)
    finally:
        conn.close()

    set_setting("col_fio",       col_fio)
    set_setting("col_cert",      col_cert)
    set_setting("col_app",       col_app)
    set_setting("col_birth",     col_birth)
    set_setting("col_stat",      col_stat)
    set_setting("last_upload",   datetime.utcnow().isoformat(sep=" ", timespec="seconds"))
    set_setting("last_filename", file.filename or "")

    return RedirectResponse(f"/admin?uploaded={len(to_insert)}", status_code=302)