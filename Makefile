.PHONY: install lint test demo demo-u0100 demo-p0420 demo-all ui build clean help

PYTHON ?= python3
POETRY ?= poetry

# Strip PYTHONPATH so a system-wide ROS / dist-packages environment cannot
# leak plugins into our poetry venv (pytest auto-discovers entry-points).
export PYTHONPATH :=
export PYTHONNOUSERSITE := 1

CHECK_API_KEY = @if [ -z "$$ANTHROPIC_API_KEY" ]; then \
	echo "ERROR: ANTHROPIC_API_KEY is not set."; exit 1; \
fi

help:
	@echo "DiagForge — make targets"
	@echo "  install     install dependencies via poetry"
	@echo "  lint        ruff + mypy --strict"
	@echo "  test        pytest with coverage"
	@echo "  demo        P0300 intermittent misfire demo"
	@echo "  demo-u0100  U0100 lost-communication demo"
	@echo "  demo-p0420  P0420 catalyst-threshold demo"
	@echo "  demo-all    run all three demo cases"
	@echo "  ui          launch the Streamlit drag-and-drop UI"
	@echo "  build       build the pip package"
	@echo "  clean       remove caches and build artifacts"

install:
	$(POETRY) install --with dev

lint:
	$(POETRY) run ruff check diagforge tests
	$(POETRY) run ruff format --check diagforge tests
	$(POETRY) run mypy --strict diagforge

test:
	$(POETRY) run pytest

demo:
	$(CHECK_API_KEY)
	$(POETRY) run diagforge analyze \
		examples/p0300_intermittent_misfire/trace.asc \
		--dtcs examples/p0300_intermittent_misfire/dtcs.json \
		--dbc  examples/p0300_intermittent_misfire/engine.dbc \
		--output ./demo-output/p0300/

demo-u0100:
	$(CHECK_API_KEY)
	$(POETRY) run diagforge analyze \
		examples/u0100_lost_comm/trace.asc \
		--dtcs examples/u0100_lost_comm/dtcs.json \
		--dbc  examples/u0100_lost_comm/engine_bus.dbc \
		--window-ms 1500 \
		--output ./demo-output/u0100/

demo-p0420:
	$(CHECK_API_KEY)
	$(POETRY) run diagforge analyze \
		examples/p0420_catalyst_threshold/trace.asc \
		--dtcs examples/p0420_catalyst_threshold/dtcs.json \
		--dbc  examples/p0420_catalyst_threshold/o2.dbc \
		--window-ms 1500 \
		--output ./demo-output/p0420/

demo-all: demo demo-u0100 demo-p0420

ui:
	$(POETRY) run streamlit run diagforge/ui/app.py

build:
	$(POETRY) build

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	rm -rf dist build demo-output
	find . -type d -name __pycache__ -exec rm -rf {} +
