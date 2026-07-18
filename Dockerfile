# Use pre-built PSP development environment
FROM pspdev/pspdev:latest

# Set PSP development environment variables (should already be set, but just in case)
ENV PSPDEV=/usr/local/pspdev
ENV PATH=$PATH:$PSPDEV/bin
ENV PSPSDK=$PSPDEV/psp/sdk

# python3 used by build.sh -> tools/make_param_sfo.py (TITLE/TITLE_8 XMB fix)
RUN if command -v apt-get >/dev/null 2>&1; then \
      apt-get update && apt-get install -y --no-install-recommends python3 && rm -rf /var/lib/apt/lists/*; \
    elif command -v apk >/dev/null 2>&1; then \
      apk add --no-cache python3; \
    else \
      echo "WARNING: could not install python3; SFO patch may be skipped (PSP_LARGE_MEMORY=1 still set in Makefile)"; \
    fi

WORKDIR /project

# Default command
CMD ["/bin/bash"]
