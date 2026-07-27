FROM python:3.11-slim
WORKDIR /app
COPY requirements-free.txt .
RUN pip install --no-cache-dir -r requirements-free.txt
COPY . .
ENV PORT=8000 PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["sh", "-c", "uvicorn free_deploy:app --host 0.0.0.0 --port ${PORT:-8000}"]
