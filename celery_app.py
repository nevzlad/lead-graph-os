from celery import Celery
from config import settings

celery_app = Celery("leadgraph_worker")
celery_app.conf.update(
    broker_url=settings.CELERY_BROKER_URL,
    result_backend=settings.CELERY_RESULT_BACKEND,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_queue="default",
    task_routes={
        "tasks.collector.*": {"queue": "collector"},
        "tasks.rewriter.*": {"queue": "rewriter"},
        "tasks.publisher.*": {"queue": "publisher"},
    },
    broker_connection_retry_on_startup=True,
    worker_max_tasks_per_child=500,
    task_soft_time_limit=300,
    task_time_limit=600
)

celery_app.autodiscover_tasks(["tasks"])
