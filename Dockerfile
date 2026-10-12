FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY action_network ./action_network
RUN pip install --no-cache-dir ".[server]"

# Mount a volume at /data to keep line-movement history across restarts.
ENV AN_HISTORY_DB=/data/odds_history.sqlite \
    PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn action_network.api:app --host 0.0.0.0 --port ${PORT}"]
