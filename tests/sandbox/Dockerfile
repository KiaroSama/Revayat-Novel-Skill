FROM python:3.14.8-slim-trixie@sha256:f85c5697265c178cc6887276c55fe16cf3d14ca35c3df6a5eab3b360534a55d2

# The build context contains only these reviewed manifests; no target source.
COPY requirements.txt constraints-ci.txt /preparation/
RUN python -m pip install --no-cache-dir --only-binary=:all: \
    -r /preparation/requirements.txt -c /preparation/constraints-ci.txt \
    && python -m pip check \
    && mkdir /target /control /scratch
USER 65532:65532
