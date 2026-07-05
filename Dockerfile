FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run once and exit — this IS the daily job, not a long-running server.
ENTRYPOINT ["python", "main.py"]