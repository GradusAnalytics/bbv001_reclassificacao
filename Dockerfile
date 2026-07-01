FROM python:3.11
WORKDIR /opt/app

# LibreOffice Calc para converter .xlsb "fora do padrão" -> .xlsx
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-calc \
        fonts-dejavu \
    && rm -rf /var/lib/apt/lists/*

# HOME gravável (seguro para o LibreOffice, mesmo com perfil isolado)
ENV HOME=/tmp

COPY bbv001_reclassificacao.py /opt/app/
COPY ecs_handler.py /opt/app/
COPY requirements.txt /opt/app/
COPY engine/ /opt/app/engine/
# Install any dependencies
RUN pip install --no-cache-dir -r requirements.txt
CMD python3 ecs_handler.py
