web: uvicorn api.main:app --host 0.0.0.0 --port $PORT
worker: celery -A celery_app worker -Q collector,rewriter,publisher,publisher_dlq --loglevel=info -c 2
bot: python -m bot.main
