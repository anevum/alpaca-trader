FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY infra/schedule_registry.json ./infra/schedule_registry.json
ENV PYTHONUNBUFFERED=1
CMD ["uvicorn", "app.orchestration_scheduler:app", "--host", "0.0.0.0", "--port", "8080"]
