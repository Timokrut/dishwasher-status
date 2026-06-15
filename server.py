from datetime import datetime, timedelta
from collections import defaultdict

from fastapi import FastAPI, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    DateTime,
    func,
)

from sqlalchemy.orm import (
    declarative_base,
    sessionmaker,
)

app = FastAPI()

app.mount(
    "/static",
    StaticFiles(directory="static"),
    name="static",
)

templates = Jinja2Templates(
    directory="templates"
)

engine = create_engine(
    "sqlite:///data/dishwasher.db",
    connect_args={
        "check_same_thread": False
    },
)

SessionLocal = sessionmaker(
    bind=engine
)

Base = declarative_base()


class Event(Base):
    __tablename__ = "events"

    id = Column(
        Integer,
        primary_key=True
    )

    user = Column(
        String,
        nullable=False
    )

    action = Column(
        String,
        nullable=False
    )

    created_at = Column(
        DateTime,
        default=datetime.utcnow
    )


Base.metadata.create_all(engine)


# ---------- helpers ----------

def humanize(dt):
    diff = datetime.utcnow() - dt

    s = int(diff.total_seconds())

    if s < 60:
        return "только что"

    m = s // 60
    if m < 60:
        return f"{m} мин назад"

    h = m // 60
    if h < 24:
        return f"{h} ч назад"

    d = h // 24
    if d < 7:
        return f"{d} дн назад"

    w = d // 7
    if w < 5:
        return f"{w} нед назад"

    mo = d // 30
    if mo < 12:
        return f"{mo} мес назад"

    y = d // 365
    return f"{y} г назад"


def get_period_query(query, period):
    now = datetime.utcnow()

    if period == "week":
        query = query.filter(
            Event.created_at >=
            now - timedelta(days=7)
        )

    elif period == "month":
        query = query.filter(
            Event.created_at >=
            now - timedelta(days=30)
        )

    elif period == "year":
        query = query.filter(
            Event.created_at >=
            now - timedelta(days=365)
        )

    return query


# ---------- home ----------

@app.get("/")
def index(request: Request):
    db = SessionLocal()

    events = (
        db.query(Event)
        .order_by(Event.created_at.desc())
        .limit(20)
        .all()
    )

    for e in events:
        e.relative_time = humanize(
            e.created_at
        )

    last_event = (
        events[0]
        if events
        else None
    )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "events": events,
            "last_event": last_event,
        },
    )


@app.post("/event")
def add_event(
    user: str = Form(...),
    action: str = Form(...),
):
    db = SessionLocal()

    db.add(
        Event(
            user=user,
            action=action,
        )
    )

    db.commit()

    return RedirectResponse(
        "/",
        status_code=303,
    )


# ---------- stats ----------

@app.get("/stats")
def stats(
    request: Request,
    user: str = "Тима",
    period: str = "all",
):
    db = SessionLocal()

    query = db.query(Event)
    query = get_period_query(
        query,
        period,
    )

    leaderboard = (
        query.with_entities(
            Event.user,
            func.count(Event.id)
        )
        .group_by(Event.user)
        .all()
    )

    leaderboard = sorted(
        leaderboard,
        key=lambda x: x[1],
        reverse=True,
    )

    leader_max = max(
        [x[1] for x in leaderboard],
        default=1,
    )

    heatmap_query = (
        query.filter(
            Event.user == user
        )
        .with_entities(
            func.date(Event.created_at),
            func.count(Event.id)
        )
        .group_by(
            func.date(Event.created_at)
        )
        .all()
    )

    heatmap_data = {
        str(day): count
        for day, count
        in heatmap_query
    }

    cells = []

    for i in range(365):
        day = (
            datetime.utcnow().date()
            - timedelta(days=364 - i)
        )

        count = heatmap_data.get(
            str(day),
            0,
        )

        if count == 0:
            level = 0
        elif count == 1:
            level = 1
        elif count <= 3:
            level = 2
        elif count <= 5:
            level = 3
        else:
            level = 4

        cells.append(
            {
                "date": day,
                "count": count,
                "level": level,
            }
        )

    month_labels = []

    current_month = None

    for i, cell in enumerate(cells):
        date = cell["date"]

        if date.month != current_month:
            current_month = date.month

            month_labels.append({
                "index": i // 7,
                "name": date.strftime("%b"),
            })

    total = sum(
        x["count"]
        for x in cells
    )

    best_day = max(
        [x["count"] for x in cells],
        default=0,
    )

    streak = 0

    for c in reversed(cells):
        if c["count"] > 0:
            streak += 1
        else:
            break

    badges = []

    if total >= 10:
        badges.append(
            "🥉 10 действий"
        )

    if total >= 25:
        badges.append(
            "🥈 25 действий"
        )

    if total >= 50:
        badges.append(
            "🥇 50 действий"
        )

    if total >= 100:
        badges.append(
            "👑 Король посудомойки"
        )

    return templates.TemplateResponse(
        request=request,
        name="stats.html",
        context={
            "leaderboard": leaderboard,
            "selected_user": user,
            "selected_period": period,
            "cells": cells,
            "total": total,
            "best_day": best_day,
            "streak": streak,
            "badges": badges,
            "leader_max": leader_max,
            "month_labels": month_labels,
        },
    )
