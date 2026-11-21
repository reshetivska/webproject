from celery import Celery

from app.config import REDIS_URL

celery_app = Celery("webproject", broker=REDIS_URL, backend=REDIS_URL, include=["app.tasks"])

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    result_expires=3600,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 600},
)
