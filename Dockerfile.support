FROM python:3.12-slim
WORKDIR /app
COPY requirements-support.txt .
RUN pip install --no-cache-dir -r requirements-support.txt
COPY app/__init__.py ./app/__init__.py
COPY app/agent_support ./app/agent_support
ENV PYTHONUNBUFFERED=1
CMD ["uvicorn", "app.agent_support.service:app", "--host", "0.0.0.0", "--port", "8080"]
