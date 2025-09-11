# Makefile for kick-monitor development

.PHONY: help install install-dev test test-cov lint format clean docs run

# Default target
help:
	@echo "Available targets:"
	@echo "  install     - Install production dependencies"
	@echo "  install-dev - Install development dependencies"
	@echo "  test        - Run tests"
	@echo "  test-cov    - Run tests with coverage"
	@echo "  lint        - Run linting and type checking"
	@echo "  format      - Format code with black and isort"
	@echo "  clean       - Clean up build artifacts"
	@echo "  docs        - Build documentation"
	@echo "  run         - Run the application"

# Installation
install:
	pip install -e .

install-dev:
	pip install -e .[dev,test]
	pip install -r requirements-dev.txt
	pre-commit install

# Testing
test:
	pytest tests/

test-cov:
	pytest --cov=src --cov-report=html --cov-report=term tests/

# Linting and formatting
lint:
	flake8 src/ tests/
	mypy src/
	bandit -r src/ -f json

format:
	black src/ tests/
	isort src/ tests/

# Cleaning
clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf .pytest_cache/
	rm -rf .mypy_cache/
	rm -rf .coverage
	rm -rf htmlcov/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

# Documentation
docs:
	cd docs && make html

# Running
run:
	python -m src.cli.main

run-manual:
	python -m src.cli.main start --manual

run-daemon:
	python -m src.cli.main start --daemon

# Development utilities
check: lint test
	@echo "All checks passed!"

setup: install-dev
	@echo "Development environment setup complete!"

# Database utilities
db-migrate:
	python -m src.cli.main db migrate

db-health:
	python -m src.cli.main db health

# Configuration utilities
config-validate:
	python -m src.cli.main config validate

config-show:
	python -m src.cli.main config show