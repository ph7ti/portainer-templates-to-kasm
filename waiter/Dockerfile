FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    WAITER_PORT=8080

WORKDIR /app

COPY waiter/wait_server.py ./

EXPOSE 8080

CMD ["python", "-u", "wait_server.py"]
