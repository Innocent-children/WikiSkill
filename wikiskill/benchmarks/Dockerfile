FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends bash libreoffice-calc \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir 'openpyxl>=3.1,<4' 'pandas>=2,<3' 'numpy>=1.26,<3'
WORKDIR /workspace/task
ENV HOME=/tmp
