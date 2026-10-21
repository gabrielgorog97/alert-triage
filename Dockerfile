FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir build==1.6.1 && python -m build --wheel --outdir /dist .

FROM python:3.12-slim
COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm /tmp/*.whl \
    && useradd --create-home --uid 1000 triage
USER triage
WORKDIR /work
ENTRYPOINT ["alert-triage"]
CMD ["--help"]
