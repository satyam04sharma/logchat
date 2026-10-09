PYTHON ?= python3.12
.PHONY: install start stop status test build export legacy-up legacy-down
install:
	$(PYTHON) -m pip install .
	logchat install
start:
	logchat start
stop:
	logchat local stop
status:
	logchat local status
test:
	$(PYTHON) -m pytest -q
	node tests/native_ui_contract.cjs
build:
	$(PYTHON) -m build
	$(PYTHON) scripts/check_release_artifacts.py
	$(PYTHON) scripts/check_release_wheel.py dist/logchat-0.3.0rc12-py3-none-any.whl
export:
	$(PYTHON) scripts/prepare_public_source.py --output "$(OUTPUT)"
# Historical Postgres stack; explicit source-checkout-only maintenance targets.
legacy-up:
	docker compose --env-file .logchat/.secrets --profile docker up -d --build --wait --wait-timeout 300
legacy-down:
	docker compose --env-file .logchat/.secrets down
