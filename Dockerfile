FROM python:3.12-alpine

WORKDIR /app

ENV PYTHONUNBUFFERED=1

RUN apk add --no-cache iputils ca-certificates && \
    addgroup -S ops && \
    adduser -S ops -G ops && \
    mkdir -p /data && \
    chown -R ops:ops /data

COPY ubiquiti_ops ./ubiquiti_ops

USER ops

EXPOSE 8090

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=3).read()"

CMD ["python", "-m", "ubiquiti_ops"]
