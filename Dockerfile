FROM python:3.12-slim

# ffprobe checks every download before it replaces a file (English audio, Dolby Vision with an HDR10 base)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY refetcharr ./refetcharr
RUN pip install --no-cache-dir . && rm -rf /root/.cache

ENV PYTHONUNBUFFERED=1 TERM=xterm-256color COLORTERM=truecolor REFETCHARR_DATA=/config PORT=8787
EXPOSE 8787
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD curl -fsS http://127.0.0.1:8787/api/health || exit 1
CMD ["refetcharr", "serve"]
