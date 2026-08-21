FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN git clone https://github.com/rdellamonica/pygro.git /app \
    && git checkout d49f8dd96e655b5a2ff07a40002ad61f71bed881 \
    && git clean -fdx \
    && cp -a /app /opt/pygro-baseline

RUN python -m pip install --no-cache-dir \
        "Cython>=3,<4" \
        "poetry-core>=2,<3" \
        "pytest>=8,<10" \
        "setuptools>=75" \
    && python -m pip install --no-cache-dir --no-build-isolation -e /app

RUN chmod -R a-w /opt/pygro-baseline \
    && mkdir -p /logs \
    && chmod 0777 /logs
