FROM python:3.11-slim
 
# git is required by manifest_sync.py to pull/push manifest.json
RUN apt-get update && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
 
WORKDIR /app
 
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
 
COPY . .
 
# Run once and exit — this IS the daily job, not a long-running server.
ENTRYPOINT ["python", "main.py"]
 