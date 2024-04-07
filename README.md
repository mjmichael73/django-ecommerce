### RabbitMQ Panel

    url: http://localhost:15672
    username: guest
    password: guest

### Running a celery worker:

    celery -A ecommerce worker -l info
    celery -A ecommerce worker -l info -P solo (on Windows)
    celery -A ecommerce flower (on Windows and Linux) (To see flower dashboard)


### weasyprint library
    In order for this library to work you should follow these instructions: https://doc.courtbouillon.org/weasyprint/stable/first_steps.html
    In windows you have to do this:
    - Install https://www.msys2.org/
    - Open msys2 (Terminal) and run these commands:
    - pacman -S mingw-w64-x86_64-gtk4
    - pacman -S mingw-w64-x86_64-python-gobject
    - Copy this: C:\msys64\mingw64\bin to your PATH
    - Close your terminal or even maybe remove venv folder and then reinstall the requirements.txt
    - Your good to go.
