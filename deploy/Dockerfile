FROM python:3.12-slim

RUN apt-get -y update && apt-get -y install dumb-init curl && apt-get clean

# Add a user to run the app
RUN useradd -m -U -d /app arduino

WORKDIR /app
USER arduino

COPY --chown=arduino:arduino requirements.txt /app/requirements.txt

RUN python3 -m venv /app/venv && . /app/venv/bin/activate && \
    pip install --no-cache -r /app/requirements.txt

COPY --chown=arduino:arduino *.py /app/
COPY --chown=arduino:arduino deps /app/deps
COPY --chown=arduino:arduino deploy/start.sh /app/start.sh

RUN chmod +x /app/start.sh

ENTRYPOINT ["/app/start.sh"]
