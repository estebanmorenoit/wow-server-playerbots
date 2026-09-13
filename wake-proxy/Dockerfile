# docker-cli (not just the daemon) + the compose plugin, so this container
# can shell out to `docker compose` against the HOST's Docker socket —
# this is "docker outside of docker" (socket-mounted), not real DinD.
FROM python:3.12-alpine
RUN apk add --no-cache docker-cli docker-cli-compose
COPY wake_proxy.py /wake_proxy.py
CMD ["python3", "-u", "/wake_proxy.py"]
