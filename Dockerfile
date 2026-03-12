FROM python:3.11-slim

WORKDIR /app

# Copy requirements first (for better caching)
COPY requirements-free.txt .
RUN pip install --no-cache-dir -r requirements-free.txt

# Copy all application files
COPY free_deploy.py .
COPY game_engine.py .
COPY database.py .
COPY wallet.py .
COPY config.py .
COPY cards.json .
COPY webapp/ ./webapp/

# Create data directory
RUN mkdir -p data

# Set environment variables
ENV PORT=8000
ENV PYTHONUNBUFFERED=1

# Expose the port
EXPOSE 8000

# Run the application
CMD uvicorn free_deploy:app --host 0.0.0.0 --port $PORT
