FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install .

EXPOSE 8000 5000
CMD ["uvicorn", "bike_mlops.api:app", "--host", "0.0.0.0", "--port", "8000"]
