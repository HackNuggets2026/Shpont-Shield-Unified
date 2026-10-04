FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY controllayer ./controllayer
RUN pip install --no-cache-dir .
COPY policy.yaml scenarios.yaml ./
COPY feeds ./feeds
COPY demo ./demo
COPY deploy ./deploy
EXPOSE 8787
CMD ["python", "-m", "controllayer", "--host", "0.0.0.0", "--port", "8787", "--policy", "/app/policy.yaml"]
