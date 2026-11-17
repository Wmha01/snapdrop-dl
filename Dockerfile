FROM python:3.11-slim

WORKDIR /app

# Instalamos FFmpeg (vital para MP4, MP3 e incrustar portadas) y curl
RUN apt-get update && \
    apt-get install -y ffmpeg curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --upgrade -r requirements.txt

COPY . .

EXPOSE 5000

# Gunicorn con soporte para múltiples hilos (evita que el canal SSE congele el servidor)
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "8", "--timeout", "120", "servidor:app"]
