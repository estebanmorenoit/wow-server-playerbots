# pymysql is the one non-stdlib dependency here (pure-Python, no compiled
# extension) — connects straight to ac-database over the network rather
# than shelling out to `docker exec`, so this container needs neither
# docker.sock nor a bundled mysql client, unlike wake-proxy.
FROM python:3.12-alpine
RUN pip install --no-cache-dir pymysql==1.1.1
COPY status_page.py index.html /
CMD ["python3", "-u", "/status_page.py"]
