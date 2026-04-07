# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# WeasyPrint / Cairo / Pango stack (see https://doc.courtbouillon.org/weasyprint/stable/first_steps.html)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libharfbuzz0b \
        libharfbuzz-subset0 \
        libffi-dev \
        shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY be/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY be/ecommerce /app

ENV DJANGO_SETTINGS_MODULE=ecommerce.settings
# Bake collected static files into the image so Celery workers can render PDFs without sharing the web container FS.
RUN python manage.py collectstatic --noinput

COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "3", "ecommerce.wsgi:application"]
