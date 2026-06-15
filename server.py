from datetime import datetime

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    DateTime,
    func,
)
from sqlalchemy.orm import declarative_base, sessionmaker

app = FastAPI()

templates = Jinja2Templates(directory="templates")

engine = create_engine(
    "sqlite:///dishwasher.db",
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(bind=engine)

Base = declarative_base()


class Event(Base):
    __tablename__ = "events"

    id = Column(Integer, primary_key=True)
    user = Column(String, nullable=False)
    action = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


Base.metadata.create_all(engine)


@app.get("/")
def home(request: Request):
    db = SessionLocal()

    events = (
        db.query(Event)
        .order_by(Event.created_at.desc())
        .limit(20)
        .all()
    )

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "events": events,
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

    return RedirectResponse("/", status_code=303)


@app.get("/stats")
def stats(request: Request):
    db = SessionLocal()

    users = (
        db.query(
            Event.user,
            func.count(Event.id).label("count")
        )
        .group_by(Event.user)
        .order_by(func.count(Event.id).desc())
        .all()
    )

    assembled = (
        db.query(func.count(Event.id))
        .filter(Event.action == "assembled")
        .scalar()
    )

    disassembled = (
        db.query(func.count(Event.id))
        .filter(Event.action == "disassembled")
        .scalar()
    )

    return templates.TemplateResponse(
        request=request,
        name="stats.html",
        context={
            "users": users,
            "assembled": assembled,
            "disassembled": disassembled,
        },
    )
