.PHONY: install lint test demo build clean help

PYTHON ?= python3
POETRY ?= poetry

# Strip PYTHONPATH so a system-wide ROS / dist-packages environment cannot
# leak plugins into our poetry venv (pytest auto-discovers entry-points).
export PYTHONPATH :=
export PYTHONNOUSERSITE := 1

help:
	@echo "DiagForge — make targets"
	@echo "  install   install dependencies via poetry"
	@echo "  lint      ruff + mypy --strict"
	@echo "  test      pytest with coverage"
	@echo "  demo      run the P0300 misfire demo case end-to-end"
	@echo "  build     build the pip package"
	@echo "  clean     remove caches and build artifacts"

install:
	$(POETRY) install --with dev

lint:
	$(POETRY) run ruff check diagforge tests
	$(POETRY) run ruff format --check diagforge tests
	$(POETRY) run mypy --strict diagforge

test:
	$(POETRY) run pytest

demo:
	@if [ -z "$$ANTHROPIC_API_KEY" ]; then \
		echo "ERROR: ANTHROPIC_API_KEY is not set."; \
		exit 1; \
	fi
	$(POETRY) run diagforge analyze \
		examples/p0300_intermittent_misfire/trace.asc \
		--dtcs examples/p0300_intermittent_misfire/dtcs.json \
		--output ./demo-output/

build:
	$(POETRY) build

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	rm -rf dist build
	find . -type d -name __pycache__ -exec rm -rf {} +
