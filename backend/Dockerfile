# A mirror can be used when Docker Hub limits pulls: --build-arg PYTHON_IMAGE=mirror.gcr.io/library/python:3.12-slim
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Run as an ordinary user. /data/private_media is where payslip PDFs and uploaded letters live (a mounted volume).
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin hrms \
    && mkdir -p /data/private_media && chown hrms /data/private_media
USER 10001

EXPOSE 8000
# Production default. docker-compose overrides this with runserver for development.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "30", "--access-logfile", "-"]
