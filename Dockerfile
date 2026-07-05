FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scraper.py upload_vector.py main.py ./

# manifest.json is expected to be provided at runtime (mounted or restored
# by the CI job before `docker run`) so delta detection has state from the
# previous run. If absent, main.py treats every article as ADDED - correct
# behavior for a first-ever run.

ENTRYPOINT ["python", "main.py"]
