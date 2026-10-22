# Collector: Python standard library plus the Docker CLI, so the browser
# controls can run the lab's fault functions against the host's Docker.
FROM python:3.13-slim
COPY --from=docker:28-cli /usr/local/bin/docker /usr/local/bin/docker
