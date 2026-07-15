# This Dockerfile defines the application layer that AWS Lambda MicroVMs builds
# and snapshots. The Lambda-managed MicroVM base image is selected separately in
# Terraform with `base_image_arn`; this `FROM` line is the container image used
# for the runtime files and tools layered into that MicroVM build.
FROM public.ecr.aws/docker/library/python:3.12-slim-bookworm

# Install a conservative set of tools that coding agents usually expect.
# Builders should remove tools they do not need and add organization-approved
# language runtimes, CLIs, CA certificates, or observability agents here.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash \
        ca-certificates \
        coreutils \
        curl \
        findutils \
        git \
        grep \
        gzip \
        openssh-client \
        procps \
        sed \
        tar \
        unzip \
        xz-utils \
    && rm -rf /var/lib/apt/lists/*

# Keep the runtime command server in a fixed location outside the writable
# workspace. Agent commands run in /workspace by default.
WORKDIR /opt/cognition-runtime
COPY server.py .

# Cognition's sandbox protocol treats this directory as the workspace root.
# Uploads, downloads, and commands are constrained to this directory unless a
# builder intentionally changes the server implementation.
RUN mkdir -p /workspace

# These defaults match the generated SandboxProfile. Builders may override them
# as image environment variables during MicroVM image creation.
ENV COGNITION_WORKSPACE_ROOT=/workspace
ENV COGNITION_MAX_BODY_BYTES=52428800
ENV COGNITION_MAX_CAPTURE_BYTES=2097152
ENV PORT=8080
ENV PYTHONUNBUFFERED=1

# The Lambda MicroVM proxy forwards authenticated requests from Cognition to
# this port. The command server itself does not implement public app-level auth.
EXPOSE 8080

# AWS starts this command during image creation, then snapshots the initialized
# application. Each MicroVM launched from the image resumes from that snapshot.
CMD ["python", "/opt/cognition-runtime/server.py"]
