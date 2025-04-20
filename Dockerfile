# Use a compatible Python base image with Debian
FROM python:3.11-slim-bullseye

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PLAYWRIGHT_BROWSERS_PATH=0

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    wget curl unzip gnupg libnss3 libatk-bridge2.0-0 libx11-xcb1 libxcomposite1 libxdamage1 \
    libxrandr2 libasound2 libxss1 libgtk-3-0 libgbm-dev libxshmfence-dev libx264-dev xvfb \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Install Playwright and download its browser binaries
RUN pip install playwright && playwright install --with-deps

# Copy source code
COPY . .

# Expose port
EXPOSE 8000

# Start the app (use shell form to enable env variable substitution)
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port $PORT"]
