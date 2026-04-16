FROM python:3.12-slim

# Install system dependencies
# libimage-exiftool-perl = exiftool (cross-platform Perl wrapper)
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libimage-exiftool-perl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy only the files needed for installation (not tests/docs)
COPY pyproject.toml ./
COPY src/ ./src/

# Install pyimgtag with HEIC support (pillow-heif bundles the heif library)
# No [review] extras — FastAPI server is launched separately if needed
RUN pip install --no-cache-dir -e ".[heic]"

# Cache and DB directories live in volumes at runtime
VOLUME ["/root/.cache/pyimgtag"]

ENTRYPOINT ["pyimgtag"]
CMD ["--help"]
