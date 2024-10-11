FROM python:3.11-slim

RUN apt-get -y update && apt-get -y install dumb-init curl && apt-get clean

# Add a user to run the app
RUN useradd -m -U -d /app arduino

WORKDIR /app
USER arduino

# Install Arduino CLI
RUN curl -fsSL https://raw.githubusercontent.com/arduino/arduino-cli/master/install.sh | sh && \
    /app/bin/arduino-cli core install arduino:avr@1.8.6 arduino:esp32@2.0.18-20240930.arduino3 arduino:mbed_nano@4.1.5
ENV ARDUINO_CLI_PATH=/app/bin/arduino-cli

COPY --chown=arduino:arduino requirements.txt /app/requirements.txt

RUN python3 -m venv /app/venv && . /app/venv/bin/activate && \
    pip install --no-cache -r /app/requirements.txt

COPY --chown=arduino:arduino *.py /app/
COPY --chown=arduino:arduino deps /app/deps
COPY --chown=arduino:arduino deploy/start.sh /app/start.sh
RUN chmod +x /app/start.sh

ENTRYPOINT ["/app/start.sh"]
