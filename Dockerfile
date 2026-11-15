FROM ghcr.io/astral-sh/uv:bookworm-slim AS build
ENV UV_PYTHON_INSTALL_DIR=/py UV_PYTHON_PREFERENCE=only-managed UV_COMPILE_BYTECODE=0 UV_LINK_MODE=copy
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends binutils >/dev/null && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv python install 3.12 && uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --no-editable
RUN mkdir -p /home/nonroot && HOME=/home/nonroot /app/.venv/bin/python -c "import duckdb; duckdb.sql('INSTALL httpfs')" \
 && find /app/.venv /py -name '*.so*' -type f -exec strip --strip-unneeded {} + \
 && find /app/.venv /py -depth \( -name __pycache__ -o -name tests -o -name test -o -name idlelib -o -name tkinter -o -name ensurepip \) -exec rm -rf {} + \
 && rm -rf /py/*/include /py/*/lib/python3.12/config-* /py/*/lib/libtcl* /py/*/lib/libtk* /py/*/lib/tcl* /py/*/lib/tk* /py/*/lib/itcl* /py/*/lib/thread*

FROM gcr.io/distroless/cc-debian12:nonroot
COPY --from=build /py /py
COPY --from=build /app/.venv /app/.venv
COPY --from=build --chown=65532:65532 /home/nonroot/.duckdb /home/nonroot/.duckdb
ENV HOME=/home/nonroot PYTHONUNBUFFERED=1
EXPOSE 8000
ENTRYPOINT ["/app/.venv/bin/electiondata-mcp"]
CMD ["--http", "--host", "0.0.0.0", "--port", "8000"]
