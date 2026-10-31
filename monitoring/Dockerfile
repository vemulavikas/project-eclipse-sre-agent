FROM prom/prometheus:v3.5.0

COPY monitoring/prometheus.render.yml /etc/prometheus/prometheus.yml

CMD ["--config.file=/etc/prometheus/prometheus.yml", "--config.expand-env", "--storage.tsdb.path=/prometheus"]
