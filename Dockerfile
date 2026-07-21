# Optional container image (works on Fly.io, Railway, Cloud Run, etc.).
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Collect static at build time (safe defaults; no DB needed for this step).
RUN python manage.py collectstatic --noinput || true
EXPOSE 8000
CMD ["gunicorn", "semfloor.wsgi", "--bind", "0.0.0.0:8000"]
