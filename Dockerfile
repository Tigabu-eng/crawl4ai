# Use Python base image
FROM python:3.11-slim

# Prevents Python from writing .pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Install system dependencies required by Playwright
RUN apt-get update && apt-get install -y \
    wget gnupg curl unzip fonts-liberation libatk-bridge2.0-0 libnspr4 libnss3 libxss1 \
    libasound2 libatk1.0-0 libdbus-1-3 libgdk-pixbuf2.0-0 libx11-xcb1 libxcomposite1 libxdamage1 \
    libxrandr2 libgbm1 libgtk-3-0 libxshmfence-dev libxext6 libxfixes3 libxrender1 libxcb1 \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Install Playwright and download required browsers
RUN pip install playwright && playwright install --with-deps

# Copy the entire project
COPY . .

# Run the app
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
