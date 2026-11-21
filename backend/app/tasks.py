import json
import socket
import threading
import time

from celery import chord
from celery.signals import worker_ready

from app import config, rds
from app.celery_app import celery_app
from app.db import SessionLocal, Task, utcnow
from app.imaging import (convolve_strip, from_b64, make_kernel, to_png_b64,
                         to_png_bytes)

import numpy as np

r = rds.r


@celery_app.task(bind=True, name="process_strip")
def process_strip(self, task_id, index, png_b64, radius, filter_name, pad_top, pad_bottom):
    worker = self.request.hostname or socket.gethostname()

    if r.exists(rds.k_cancel(task_id)) or r.exists(rds.k_failed(task_id)):
        return {"index": index, "skipped": True}

    r.incr(rds.k_started(task_id))
    r.incr("busy:" + worker)
    t0 = time.time()
    try:
        strip = from_b64(png_b64)
        kernel = make_kernel(filter_name, radius)

        def on_row():
            r.incr(rds.k_progress(task_id))

        def should_stop():
            return r.exists(rds.k_cancel(task_id)) or r.exists(rds.k_failed(task_id))

        result = convolve_strip(strip, kernel, pad_top, pad_bottom, on_row, should_stop)
        if result is None:
            return {"index": index, "skipped": True}

        h, w = result.shape[:2]
        sec_per_unit = (time.time() - t0) / (h * w * (2 * radius + 1) ** 2)
        old = float(r.get("stats:unit_sec") or config.DEFAULT_UNIT_SEC)
        r.set("stats:unit_sec", 0.7 * old + 0.3 * sec_per_unit)

        r.incr(rds.k_finished(task_id))
        r.hincrby(rds.k_workers(task_id), worker, 1)
        r.hincrby("stats:processed", worker, 1)
        return {"index": index, "data": to_png_b64(result)}
    except Exception as e:
        r.set(rds.k_failed(task_id), str(e)[:200], ex=config.KEY_TTL)
        return {"index": index, "skipped": True}
    finally:
        r.decr("busy:" + worker)


@celery_app.task(bind=True, name="finalize_task")
def finalize_task(self, results, task_id):
    db = SessionLocal()
    try:
        task = db.query(Task).filter(Task.id == task_id).with_for_update().first()
        if task is None or task.status != "active":
            return
        if r.exists(rds.k_cancel(task_id)):
            return

        workers = r.hgetall(rds.k_workers(task_id))
        error = r.get(rds.k_failed(task_id))
        parts = sorted([x for x in results if x and not x.get("skipped")], key=lambda x: x["index"])
        if error is None and len(parts) != task.total_strips:
            error = "Не всі смуги були пораховані"

        if error:
            task.status = "failed"
            task.error = error
        else:
            full = np.vstack([from_b64(p["data"]) for p in parts])
            task.result_image = to_png_bytes(full)
            task.status = "done"

        task.workers_used = json.dumps(workers)
        task.finished_at = utcnow()
        task.duration_sec = (task.finished_at - task.created_at).total_seconds()
        db.commit()
    except Exception as e:
        db.rollback()
        task = db.get(Task, task_id)
        if task is not None and task.status == "active":
            task.status = "failed"
            task.error = "Помилка складання результату: %s" % e
            task.finished_at = utcnow()
            db.commit()
    finally:
        db.close()


@worker_ready.connect
def start_heartbeat(sender=None, **kwargs):
    name = getattr(sender, "hostname", None) or socket.gethostname()
    try:
        concurrency = int(sender.controller.concurrency)
    except Exception:
        concurrency = 1
    r.set("busy:" + name, 0)

    def loop():
        while True:
            try:
                r.setex("worker:" + name, 15, concurrency)
            except Exception:
                pass
            time.sleep(5)

    threading.Thread(target=loop, daemon=True).start()
