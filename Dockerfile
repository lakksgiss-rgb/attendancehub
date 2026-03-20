FROM python:3.10-slim

WORKDIR /app

# Install system dependencies for optional packages (e.g., pillow, opencv)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender1 \
        libjpeg-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

ENV PYTHONUNBUFFERED=1
ENV PORT=8000

EXPOSE 8000

CMD ["gunicorn", "ams.ams.wsgi", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4"]
