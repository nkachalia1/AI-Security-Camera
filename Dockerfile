FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 libgl1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md requirements.txt ./
COPY src ./src
COPY models ./models

RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir .

EXPOSE 8080
CMD ["vision-appliance"]

