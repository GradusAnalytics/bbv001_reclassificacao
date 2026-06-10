FROM python:3.11
WORKDIR /opt/app

COPY dummy_repo.py /opt/app/
COPY ecs_handler.py /opt/app/
COPY requirements.txt /opt/app/

# Install any dependencies
RUN pip install --no-cache-dir -r requirements.txt

CMD python3 ecs_handler.py
