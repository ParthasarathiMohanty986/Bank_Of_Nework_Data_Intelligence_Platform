FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 SETTLEMENT_DB=/data/settlement.db
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home --uid 10001 settlement && mkdir /data && chown settlement:settlement /data
COPY settlement ./settlement
USER settlement
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=2)"
CMD ["python", "-m", "uvicorn", "settlement.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
