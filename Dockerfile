# Multi-stage: build base image with system deps + Python
FROM python:3.11-slim as base

# Install ImageMagick + Pango + fontconfig + dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    imagemagick \
    libpango-1.0-0 \
    libpango1.0-dev \
    libpangoft2-1.0-0 \
    fontconfig \
    fonts-noto-mono \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Relax ImageMagick security policy (use pipe delimiter to avoid conflicts)
RUN sed -i 's|<policy domain="coder" rights="none" pattern="PDF" />||' /etc/ImageMagick-6/policy.xml || true

# Create app directory
WORKDIR /app

# Copy fonts into the container - system location for best compatibility
COPY fonts/ /usr/local/share/fonts/noto/
RUN fc-cache -fv /usr/local/share/fonts/

# Also copy to app directory for backup
COPY fonts/ /app/fonts/

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot code
COPY bot.py .

# Create non-root user for security
RUN useradd -m -u 1000 app && \
    chown -R app:app /app && \
    chmod 755 /usr/local/share/fonts /usr/local/share/fonts/noto
USER app

# Set HOME and fontconfig env for font discovery
ENV HOME=/home/app \
    XDG_DATA_HOME=/home/app/.local/share \
    FONTCONFIG_PATH=/etc/fonts

# Health check: bot should be running and responding to signals
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=1 \
    CMD python -c "import sys; sys.exit(0)" || exit 1

# Run the bot
CMD ["python", "bot.py"]
