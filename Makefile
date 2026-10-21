PYTHON ?= python3
VENV := .venv
BIN := $(VENV)/bin

.PHONY: help install lint format test build demo demo-details demo-json docker clean ci

help:
	@echo "install    create .venv and install the package with dev tools"
	@echo "lint       ruff lint and format check"
	@echo "format     apply ruff formatting"
	@echo "test       run the test suite"
	@echo "build      build the wheel and sdist into dist/"
	@echo "demo       run the CLI on examples/: short table + demo-run/report.html"
	@echo "demo-details  same, with impact and checklists in the terminal"
	@echo "demo-json  same, with JSON output"
	@echo "docker     build the container image"
	@echo "ci         lint + test + build (what CI runs)"

$(BIN)/activate: pyproject.toml
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --quiet --upgrade pip
	$(BIN)/pip install --quiet -e '.[dev]'
	@touch $@

install: $(BIN)/activate

lint: install
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

format: install
	$(BIN)/ruff format .

test: install
	$(BIN)/pytest -q

build: install
	rm -rf dist
	$(BIN)/python -m build --outdir dist .

demo: install
	rm -rf demo-run
	-$(BIN)/alert-triage run examples/alerts --catalog examples/services.yaml \
		--state demo-run/state.json --outbox demo-run/outbox --html demo-run/report.html

demo-details: install
	rm -rf demo-run
	-$(BIN)/alert-triage run examples/alerts --catalog examples/services.yaml \
		--state demo-run/state.json --outbox demo-run/outbox --details

demo-json: install
	rm -rf demo-run
	-$(BIN)/alert-triage run examples/alerts --catalog examples/services.yaml \
		--state demo-run/state.json --outbox demo-run/outbox --json

docker:
	docker build -t alert-triage:local .

clean:
	rm -rf $(VENV) dist build demo-run state outbox .pytest_cache .ruff_cache src/*.egg-info

ci: lint test build
