FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .
RUN useradd --create-home appuser
USER appuser
ENTRYPOINT ["fraudstream"]
CMD ["--count", "1000", "--output", "/tmp/scored_transactions.jsonl"]

