FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml ./
COPY src ./src
RUN python -m pip install --no-cache-dir build && python -m build --wheel

FROM python:3.12-slim
RUN useradd --create-home --uid 10001 fleetplane
WORKDIR /app
COPY --from=builder /build/dist/*.whl /tmp/fleetplane.whl
RUN python -m pip install --no-cache-dir /tmp/fleetplane.whl && rm /tmp/fleetplane.whl
USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["fleetplane"]
CMD ["demo-api", "--host", "0.0.0.0", "--port", "8000"]
