FROM prom/prometheus:v3.5.0 AS prometheus

FROM python:3.12-slim

COPY --from=prometheus /bin/prometheus /usr/local/bin/prometheus
COPY monitoring/prometheus.render.yml /etc/prometheus/prometheus.yml
COPY monitoring/prometheus_proxy.py /app/prometheus_proxy.py

ENV PYTHONUNBUFFERED=1

CMD ["python", "/app/prometheus_proxy.py"]
