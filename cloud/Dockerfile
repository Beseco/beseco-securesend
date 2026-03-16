FROM python:3.12-slim

WORKDIR /app

# Install dependencies first for better layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the cloud package
COPY . .

# Add the project root to PYTHONPATH so `from core.storage import ...` works.
# At runtime the project root is mounted at /project, and the cloud/ dir is /app.
# docker-compose mounts <host_project_root> → /project via a volume.
ENV PYTHONPATH=/project

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
