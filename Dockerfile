# Pinned to the linux/amd64+linux/arm64 manifest list digest of python:3.12-slim
# published at https://hub.docker.com/_/python. Bump via Dependabot's `docker`
# ecosystem or manually when a security patch is needed.
FROM python:3.12-slim@sha256:804ddf3251a60bbf9c92e73b7566c40428d54d0e79d3428194edf40da6521286

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

# Run as an unprivileged user so a container escape is not an immediate
# privilege-escalation. Pre-create and chown the cache dir before the VOLUME
# declaration so the mounted volume inherits non-root ownership at first run.
RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin pyimgtag \
    && mkdir -p /home/pyimgtag/.cache/pyimgtag \
    && chown -R pyimgtag:pyimgtag /home/pyimgtag/.cache
USER pyimgtag
WORKDIR /home/pyimgtag

VOLUME ["/home/pyimgtag/.cache/pyimgtag"]

ENTRYPOINT ["pyimgtag"]
CMD ["--help"]
