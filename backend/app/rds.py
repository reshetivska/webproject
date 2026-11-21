import redis

from app.config import REDIS_URL

r = redis.Redis.from_url(REDIS_URL, decode_responses=True)


def k_progress(task_id):
    return f"task:{task_id}:progress"


def k_started(task_id):
    return f"task:{task_id}:started"


def k_finished(task_id):
    return f"task:{task_id}:finished"


def k_cancel(task_id):
    return f"task:{task_id}:cancel"


def k_failed(task_id):
    return f"task:{task_id}:failed"


def k_workers(task_id):
    return f"task:{task_id}:workers"
