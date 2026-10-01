FROM python:3.13-slim

WORKDIR /workspace

COPY foundation/migrator/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY db /workspace/db
COPY foundation/migrator /workspace/foundation/migrator

CMD ["python", "-m", "foundation.migrator.run"]
