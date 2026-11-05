"""A small, shared kitchen check-in board. Existing events stay compatible."""
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import Column, DateTime, Integer, String, create_engine, func, text
from sqlalchemy.orm import declarative_base, sessionmaker

ROOT = Path(__file__).resolve().parent
LOCAL_TZ = ZoneInfo(os.getenv("DISHWASHER_TIMEZONE", "Europe/Moscow"))
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{ROOT / 'data' / 'dishwasher.db'}")
if DATABASE_URL.startswith("sqlite:///") and DATABASE_URL != "sqlite:///:memory:":
    Path(DATABASE_URL.removeprefix("sqlite:///")).expanduser().parent.mkdir(parents=True, exist_ok=True)

USERS = [
    {"name": "Тима", "initial": "Т", "color": "sage"},
    {"name": "Алиса", "initial": "А", "color": "rose"},
    {"name": "Мама", "initial": "М", "color": "sand"},
    {"name": "Папа", "initial": "П", "color": "blue"},
]
USER_NAMES = {user["name"] for user in USERS}
ACTIONS = {"disassembled": "Разгрузка", "assembled": "Загрузка"}
PERIODS = {"all": "Всё время", "week": "7 дней", "month": "30 дней", "year": "365 дней"}
UNDO_SECONDS = 120

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False, "timeout": 15})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()


class Event(Base):
    __tablename__ = "events"
    id = Column(Integer, primary_key=True)
    user = Column(String, nullable=False)
    action = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Submission(Base):
    """Idempotency receipts also survive undo, so retries never recreate a check-in."""
    __tablename__ = "submissions"
    token = Column(String(36), primary_key=True)
    event_id = Column(Integer, nullable=True, index=True)


Base.metadata.create_all(engine)
app = FastAPI(title="Посудомойка")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
templates = Jinja2Templates(directory=ROOT / "templates")


def plural(number, one, few, many):
    word = many if 11 <= number % 100 <= 14 else one if number % 10 == 1 else few if 2 <= number % 10 <= 4 else many
    return f"{number} {word}"


templates.env.filters["plural"] = plural


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def local_date(dt):
    return dt.replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ).date()


def period_start(period):
    days = {"week": 7, "month": 30, "year": 365}.get(period)
    if days is None:
        return None
    today = datetime.now(LOCAL_TZ).date()
    midnight = datetime.combine(today - timedelta(days=days - 1), datetime.min.time(), LOCAL_TZ)
    return midnight.astimezone(timezone.utc).replace(tzinfo=None)


def humanize(dt):
    seconds = max(0, int((utcnow() - dt).total_seconds()))
    for divisor, limit, unit in [(1, 60, None), (60, 60, "мин"), (3600, 24, "ч"), (86400, 7, "дн"), (604800, 5, "нед"), (2592000, 12, "мес")]:
        value = seconds // divisor
        if value < limit:
            return "только что" if unit is None else f"{value} {unit} назад"
    return f"{seconds // 31536000} г назад"


def event_view(event):
    return {"id": event.id, "user": event.user, "action": event.action,
            "label": ACTIONS.get(event.action, event.action),
            "relative_time": humanize(event.created_at),
            "iso": event.created_at.isoformat() + "Z"}


def latest_event(db):
    return db.query(Event).order_by(Event.created_at.desc(), Event.id.desc()).first()


def event_revision(event):
    # SQLite can reuse a deleted row's ID; its timestamp distinguishes a new check-in.
    return f"{event.id}:{event.created_at.isoformat()}" if event else ""


def leaderboard(db, period):
    query = db.query(Event.user, func.count(Event.id))
    start = period_start(period)
    if start:
        query = query.filter(Event.created_at >= start)
    counts = dict(query.group_by(Event.user).all())
    for name in USER_NAMES:
        counts.setdefault(name, 0)
    return sorted(counts.items(), key=lambda row: (-row[1], row[0]))


def render(request, name, **context):
    return templates.TemplateResponse(request=request, name=name, context={
        "users": USERS, "periods": PERIODS, "page": "home", **context,
    }, headers={"Cache-Control": "no-store"})


def wants_json(request):
    return "application/json" in request.headers.get("accept", "")


@app.exception_handler(HTTPException)
def http_error(request, exc):
    if wants_json(request):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    response = render(request, "error.html", message=exc.detail)
    response.status_code = exc.status_code
    return response


@app.get("/")
def index(request: Request, saved: str = "", undone: bool = False):
    with SessionLocal() as db:
        events = db.query(Event).order_by(Event.created_at.desc(), Event.id.desc()).limit(20).all()
        last = events[0] if events else None
        receipt = db.get(Submission, saved) if len(saved) == 36 else None
        saved_event = db.get(Event, receipt.event_id) if receipt and receipt.event_id else None
        confirmed = bool(saved_event and last and saved_event.id == last.id)
        can_undo = bool(confirmed and 0 <= (utcnow() - saved_event.created_at).total_seconds() < UNDO_SECONDS)
        return render(request, "index.html", events=[event_view(e) for e in events],
                      last_event=event_view(last) if last else None,
                      revision=event_revision(last),
                      request_id=str(uuid4()), saved_token=saved if can_undo else "",
                      saved_event=event_view(saved_event) if confirmed else None,
                      undone=undone, weekly=leaderboard(db, "week"))


def validate_token(token):
    try:
        return str(UUID(token))
    except (ValueError, AttributeError):
        raise HTTPException(422, "Не удалось распознать отметку. Обновите страницу и повторите.")


@app.post("/event")
def add_event(request: Request, user: str = Form(...), action: str = Form(...), request_id: str = Form("")):
    if user not in USER_NAMES or action not in ACTIONS:
        raise HTTPException(422, "Выберите человека и действие из списка.")
    token = validate_token(request_id) if request_id else str(uuid4())
    with SessionLocal() as db:
        # Serialize check-ins and undo together, including across Uvicorn workers.
        db.execute(text("BEGIN IMMEDIATE"))
        receipt = db.get(Submission, token)
        if receipt:
            event = db.get(Event, receipt.event_id) if receipt.event_id else None
            if not event:
                raise HTTPException(409, "Эта отметка уже отменена. Обновите страницу для нового действия.")
            if event.user != user or event.action != action:
                raise HTTPException(409, "Эта отметка уже сохранена. Обновите страницу для нового действия.")
        else:
            event = Event(user=user, action=action, created_at=utcnow())
            db.add(event)
            db.flush()
            db.add(Submission(token=token, event_id=event.id))
            db.commit()
    if wants_json(request):
        return JSONResponse({"redirect": f"/?saved={token}", "id": event.id}, headers={"Cache-Control": "no-store"})
    return RedirectResponse(f"/?saved={token}", status_code=303)


@app.post("/event/undo")
def undo_event(request: Request, token: str = Form(...)):
    token = validate_token(token)
    with SessionLocal() as db:
        db.execute(text("BEGIN IMMEDIATE"))
        receipt = db.get(Submission, token)
        event = db.get(Event, receipt.event_id) if receipt and receipt.event_id else None
        if event:
            last = latest_event(db)
            if last.id != event.id or not 0 <= (utcnow() - event.created_at).total_seconds() < UNDO_SECONDS:
                raise HTTPException(409, "Можно отменить только последнюю отметку в течение двух минут.")
            receipt.event_id = None
            db.delete(event)
            db.commit()
        elif not receipt:
            raise HTTPException(404, "Отметка не найдена.")
    if wants_json(request):
        return JSONResponse({"redirect": "/?undone=true"}, headers={"Cache-Control": "no-store"})
    return RedirectResponse("/?undone=true", status_code=303)


@app.get("/api/status")
def status():
    with SessionLocal() as db:
        last = latest_event(db)
        return JSONResponse({"latest_id": last.id if last else 0, "revision": event_revision(last)}, headers={"Cache-Control": "no-store"})


@app.get("/stats")
def stats(request: Request, user: str = "Тима", period: str = "all"):
    if user not in USER_NAMES or period not in PERIODS:
        raise HTTPException(422, "Выберите человека и период из списка.")
    with SessionLocal() as db:
        query = db.query(Event).filter(Event.user == user)
        start = period_start(period)
        if start:
            query = query.filter(Event.created_at >= start)
        events = query.order_by(Event.created_at).all()
        leaders = leaderboard(db, period)
        lifetime_total = db.query(Event).filter(Event.user == user).count()
    counts = Counter(local_date(e.created_at) for e in events)
    today = datetime.now(LOCAL_TZ).date()
    streak = 0
    day = today if counts[today] else today - timedelta(days=1)
    while counts[day]:
        streak += 1
        day -= timedelta(days=1)
    days = {"week": 7, "month": 30}.get(period, 365)
    first_day = today - timedelta(days=days - 1)
    cells = [{"padding": True}] * first_day.weekday()
    month_labels = []
    previous_month = None
    months = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
    for i in range(days):
        day = first_day + timedelta(days=i)
        count = counts[day]
        if day.month != previous_month:
            month_labels.append({"index": len(cells) // 7, "name": months[day.month - 1]})
            previous_month = day.month
        cells.append({"date": day, "count": count, "level": min(count, 4), "padding": False})
    actions = Counter(e.action for e in events)
    badges = [label for threshold, label in [(10, "Первые 10 дел"), (25, "25 добрых дел"),
                                             (50, "50 поводов для спасибо"), (100, "100 дел · Хранитель порядка")]
              if lifetime_total >= threshold]
    return render(request, "stats.html", page="stats", selected_user=user, selected_period=period,
                  leaderboard=leaders, leader_max=max([count for _, count in leaders], default=1) or 1,
                  family_total=sum(count for _, count in leaders), cells=cells, month_labels=month_labels,
                  total=len(events), streak=streak, best_day=max(counts.values(), default=0),
                  unloaded=actions["disassembled"], loaded=actions["assembled"], calendar_days=days, badges=badges)
