# Small host adapter: no application dependencies or deployment data in image.
FROM docker:27-cli AS cli
FROM python:3.12-alpine
COPY --from=cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=cli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose
