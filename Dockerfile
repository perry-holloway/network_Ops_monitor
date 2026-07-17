FROM python:3.12-alpine

WORKDIR /app

RUN apk add --no-cache iputils ca-certificates && \
    addgroup -S ops && \
    adduser -S ops -G ops && \
    mkdir -p /data && \
    chown -R ops:ops /data

COPY ubiquiti_ops ./ubiquiti_ops

USER ops

EXPOSE 8090

CMD ["python", "-m", "ubiquiti_ops"]

