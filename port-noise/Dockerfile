FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DATABASE=/data/port_noise.sqlite3

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

# SQLite 数据落盘到挂载卷，容器重建不丢整季数据
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

# 单 worker + 多线程：SQLite 单文件库，配合 WAL 即可；核定顺序由库内事件序号保证
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "8", "--timeout", "120", "app:create_app()"]
