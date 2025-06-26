FROM python:3.11-slim

WORKDIR /app

# Install system dependencies including curl for ngrok
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install ngrok
RUN curl -s https://ngrok-agent.s3.amazonaws.com/ngrok.asc | tee /etc/apt/trusted.gpg.d/ngrok.asc >/dev/null && \
    echo "deb https://ngrok-agent.s3.amazonaws.com buster main" | tee /etc/apt/sources.list.d/ngrok.list && \
    apt-get update && apt-get install ngrok

# Configure ngrok with authtoken
RUN ngrok config add-authtoken 2xdVonNeO6Y46ShAouViTTjUEMg_3Tw5NqercpYnAHcZCFynp

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

EXPOSE 5001

CMD ["uvicorn", "whatsapp_app:app", "--host", "0.0.0.0", "--port", "5001"] 