FROM python:3.12-slim
RUN pip install --no-cache-dir gunicorn==23.0.0
WORKDIR /app
COPY monitor.py server.py ./
COPY web ./web
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", "--access-logfile", "-", "server:application"]
