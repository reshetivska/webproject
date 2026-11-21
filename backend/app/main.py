import io
import json
import os
import socket
import uuid
from contextlib import asynccontextmanager

import numpy as np
from celery import chord
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import config, rds
from app.auth import (create_token, get_admin, get_current_user, hash_password,
                      verify_password)
from app.db import AuthToken, Task, User, get_db, init_db, utcnow
from app.imaging import complexity, split_rows, to_png_b64
from app.tasks import finalize_task, process_strip

API_NAME = os.getenv("API_NAME", socket.gethostname())
r = rds.r


@asynccontextmanager
async def lifespan(app):
    init_db()
    yield


app = FastAPI(title="Розподілена обробка зображень", lifespan=lifespan)


@app.middleware("http")
async def add_server_header(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Served-By"] = API_NAME
    return response


STATUS_TEXT = {"queued": "У черзі", "running": "Виконується", "done": "Готово",
               "failed": "Помилка", "cancelled": "Скасовано"}


def alive_workers():
    result = []
    for key in r.scan_iter("worker:*"):
        name = key[len("worker:"):]
        result.append({
            "name": name,
            "concurrency": int(r.get(key) or 1),
            "busy": max(0, int(r.get("busy:" + name) or 0)),
            "processed": int(r.hget("stats:processed", name) or 0),
        })
    result.sort(key=lambda w: w["name"])
    return result


def total_units(task):
    return task.total_strips * (2 * task.radius + 1)


def task_complexity(task):
    return complexity(task.width, task.height, task.radius)


def estimate_wait(db, task, workers):
    slots = sum(w["concurrency"] for w in workers)
    if slots == 0:
        return None
    unit_sec = float(r.get("stats:unit_sec") or config.DEFAULT_UNIT_SEC)
    ahead = db.query(Task).filter(Task.status == "active", Task.created_at < task.created_at).all()
    work = 0.0
    for t in ahead:
        done = int(r.get(rds.k_progress(t.id)) or 0)
        work += max(0.0, 1 - done / total_units(t)) * task_complexity(t)
    return round(work * unit_sec / slots)


def task_view(task, db, workers=None):
    view = {
        "id": task.id,
        "filter": task.filter_name,
        "radius": task.radius,
        "width": task.width,
        "height": task.height,
        "created_at": task.created_at.isoformat() + "Z",
        "finished_at": task.finished_at.isoformat() + "Z" if task.finished_at else None,
        "duration_sec": task.duration_sec,
        "error": task.error,
        "workers": {},
        "wait_sec": None,
    }
    if task.status != "active":
        view["status"] = task.status
        view["progress"] = 100 if task.status == "done" else 0
        view["message"] = task.error or STATUS_TEXT[task.status]
        view["workers"] = json.loads(task.workers_used) if task.workers_used else {}
        return view

    done = int(r.get(rds.k_progress(task.id)) or 0)
    started = int(r.get(rds.k_started(task.id)) or 0)
    finished = int(r.get(rds.k_finished(task.id)) or 0)
    view["progress"] = min(99, int(done * 100 / total_units(task)))
    view["workers"] = r.hgetall(rds.k_workers(task.id))

    if started == 0:
        view["status"] = "queued"
        if workers is None:
            workers = alive_workers()
        if not workers:
            view["message"] = "У черзі: немає жодного активного воркера"
        else:
            wait = estimate_wait(db, task, workers)
            view["wait_sec"] = wait
            view["message"] = "Усі воркери зайняті, задачу поставлено в чергу. Очікування ~%s с" % wait
    else:
        view["status"] = "running"
        if finished >= task.total_strips:
            view["message"] = "Збираємо результат..."
        else:
            view["message"] = "Пораховано смуг: %d з %d" % (finished, task.total_strips)
    return view


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=30, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=6, max_length=100)


@app.post("/api/register")
def register(data: Credentials, db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == data.username).first():
        raise HTTPException(400, "Такий користувач уже існує")
    user = User(username=data.username, password_hash=hash_password(data.password))
    db.add(user)
    db.commit()
    return {"token": create_token(db, user.id)}


@app.post("/api/login")
def login(data: Credentials, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == data.username).first()
    if user is None or not verify_password(data.password, user.password_hash):
        raise HTTPException(401, "Невірний логін або пароль")
    return {"token": create_token(db, user.id)}


@app.post("/api/logout")
def logout(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    token = request.headers.get("Authorization", "")[7:]
    row = db.get(AuthToken, token)
    if row:
        db.delete(row)
        db.commit()
    return {"ok": True}


@app.get("/api/me")
def me(user: User = Depends(get_current_user)):
    return {"username": user.username, "is_admin": user.username in config.ADMIN_USERS}


@app.get("/api/health")
def health():
    return {"server": API_NAME}


@app.get("/api/limits")
def limits(user: User = Depends(get_current_user)):
    return {
        "max_side": config.MAX_IMAGE_SIDE,
        "max_file_mb": config.MAX_FILE_MB,
        "max_radius": config.MAX_RADIUS,
        "max_complexity": config.MAX_COMPLEXITY,
        "max_active": config.MAX_ACTIVE_PER_USER,
        "max_tasks": config.MAX_TASKS_PER_USER,
    }


@app.post("/api/tasks")
def create_task(
    file: UploadFile = File(...),
    filter: str = Form("blur"),
    radius: int = Form(5),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if filter not in ("blur", "sharpen"):
        raise HTTPException(400, "Невідомий фільтр")
    if not 1 <= radius <= config.MAX_RADIUS:
        raise HTTPException(400, "Радіус має бути від 1 до %d" % config.MAX_RADIUS)

    data = file.file.read(config.MAX_FILE_MB * 1024 * 1024 + 1)
    if len(data) > config.MAX_FILE_MB * 1024 * 1024:
        raise HTTPException(413, "Файл завеликий (максимум %d МБ)" % config.MAX_FILE_MB)

    try:
        img = Image.open(io.BytesIO(data))
        mime = Image.MIME.get(img.format, "image/png")
        width, height = img.size
        if max(width, height) > config.MAX_IMAGE_SIDE:
            raise HTTPException(400, "Зображення завелике: максимум %d пікселів по стороні" % config.MAX_IMAGE_SIDE)
        img = img.convert("RGB")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "Не вдалося прочитати зображення (потрібен PNG або JPEG)")

    cost = complexity(width, height, radius)
    if cost > config.MAX_COMPLEXITY:
        raise HTTPException(400, "Задача занадто трудомістка (%d із дозволених %d). Зменшіть радіус або розмір зображення."
                            % (cost, config.MAX_COMPLEXITY))

    active = db.query(Task).filter(Task.user_id == user.id, Task.status == "active").count()
    if active >= config.MAX_ACTIVE_PER_USER:
        raise HTTPException(429, "Забагато активних задач (максимум %d). Дочекайтесь завершення або скасуйте одну." % config.MAX_ACTIVE_PER_USER)
    total = db.query(Task).filter(Task.user_id == user.id).count()
    if total >= config.MAX_TASKS_PER_USER:
        raise HTTPException(400, "Досягнуто ліміт збережених задач (%d). Видаліть старі з історії." % config.MAX_TASKS_PER_USER)

    arr = np.array(img)
    strips = split_rows(height, config.N_STRIPS, radius)
    task = Task(id=str(uuid.uuid4()), user_id=user.id, filter_name=filter, radius=radius,
                width=width, height=height, total_strips=len(strips), status="active",
                mime=mime, input_image=data)
    db.add(task)
    db.commit()

    for key in (rds.k_progress(task.id), rds.k_started(task.id), rds.k_finished(task.id)):
        r.set(key, 0, ex=config.KEY_TTL)

    try:
        header = [
            process_strip.s(task.id, s.index, to_png_b64(arr[s.ys:s.ye]), radius, filter, s.pad_top, s.pad_bottom)
            for s in strips
        ]
        chord(header)(finalize_task.s(task.id))
    except Exception:
        db.delete(task)
        db.commit()
        raise HTTPException(503, "Черга задач недоступна, спробуйте пізніше")

    return task_view(task, db)


@app.get("/api/tasks")
def list_tasks(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    tasks = db.query(Task).filter(Task.user_id == user.id).order_by(Task.created_at.desc()).all()
    workers = alive_workers()
    return {"tasks": [task_view(t, db, workers) for t in tasks]}


def get_own_task(task_id, user, db, lock=False):
    q = db.query(Task).filter(Task.id == task_id, Task.user_id == user.id)
    task = (q.with_for_update() if lock else q).first()
    if task is None:
        raise HTTPException(404, "Задачу не знайдено")
    return task


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return task_view(get_own_task(task_id, user, db), db)


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = get_own_task(task_id, user, db, lock=True)
    if task.status != "active":
        raise HTTPException(400, "Задача вже завершена")
    r.set(rds.k_cancel(task.id), 1, ex=config.KEY_TTL)
    task.status = "cancelled"
    task.finished_at = utcnow()
    task.duration_sec = (task.finished_at - task.created_at).total_seconds()
    task.workers_used = json.dumps(r.hgetall(rds.k_workers(task.id)))
    db.commit()
    return task_view(task, db)


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = get_own_task(task_id, user, db, lock=True)
    if task.status == "active":
        raise HTTPException(400, "Спочатку скасуйте задачу")
    db.delete(task)
    db.commit()
    return {"ok": True}


@app.get("/api/tasks/{task_id}/original")
def get_original(task_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = get_own_task(task_id, user, db)
    return Response(content=task.input_image, media_type=task.mime or "image/png")


@app.get("/api/tasks/{task_id}/result")
def get_result(task_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = get_own_task(task_id, user, db)
    if task.status != "done" or task.result_image is None:
        raise HTTPException(404, "Результату ще немає")
    return Response(content=task.result_image, media_type="image/png")


@app.get("/api/admin/overview")
def admin_overview(admin: User = Depends(get_admin), db: Session = Depends(get_db)):
    workers = alive_workers()
    rows = (db.query(Task, User.username).join(User, User.id == Task.user_id)
            .filter(Task.status == "active").order_by(Task.created_at).all())
    active = []
    for t, username in rows:
        v = task_view(t, db, workers)
        v["username"] = username
        active.append(v)

    counts = {}
    for status in ("active", "done", "failed", "cancelled"):
        counts[status] = db.query(Task).filter(Task.status == status).count()

    return {
        "api_server": API_NAME,
        "workers": workers,
        "queue_length": r.llen("celery"),
        "autoscaler": json.loads(r.get("autoscaler:state") or "null"),
        "slots_total": sum(w["concurrency"] for w in workers),
        "slots_busy": sum(w["busy"] for w in workers),
        "active_tasks": active,
        "task_counts": counts,
        "users": db.query(User).count(),
        "unit_sec": float(r.get("stats:unit_sec") or config.DEFAULT_UNIT_SEC),
    }


app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True), name="static")
