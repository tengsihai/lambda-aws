.PHONY: install lint format typecheck test check build clean

install:
	python3 -m pip install -r requirements-dev.txt

lint:
	ruff check .

format:
	ruff format .
	ruff check --fix .

typecheck:
	mypy src tests

test:
	pytest --cov=src --cov-report=term-missing

check: lint typecheck test

# 需要本机已安装 AWS SAM CLI
build:
	sam build --template-file template.yaml

clean:
	rm -rf .aws-sam .mypy_cache .ruff_cache .pytest_cache .coverage htmlcov
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +
