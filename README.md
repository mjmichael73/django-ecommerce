### RabbitMQ Panel

    url: http://localhost:15672
    username: guest
    password: guest

### Running a celery worker:

    celery -A ecommerce worker -l info
    celery -A ecommerce worker -l info -P solo (on Windows)
    celery -A ecommerce flower (on Windows and Linux) (To see flower dashboard)