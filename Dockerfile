FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY agents/ ./agents/
COPY data/ ./data/
COPY voice_ui.html .

# Cloud Run sets PORT env var — default to 8080
ENV PORT=8080

# Run the server
CMD uvicorn agents.voice.voice_server:app --host 0.0.0.0 --port $PORT
