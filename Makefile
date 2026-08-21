.PHONY: help install install-audio install-all test lint fmt doctor status check clean

help:
	@echo "install        core dependencies only"
	@echo "install-audio  + librosa/scipy (analyze, drums transcribe)"
	@echo "install-all    + demucs/torch (stems) and dev tools"
	@echo "test           run the test suite"
	@echo "lint / fmt     ruff check / ruff format"
	@echo "doctor         check the toolchain"
	@echo "status         progress board across both albums"
	@echo "check          validate every song.yaml"
	@echo "clean          remove generated build scripts and caches"

install:
	pip install -e .

install-audio:
	pip install -e '.[audio]'

install-all:
	pip install -e '.[audio,separate,dev]'

test:
	pytest -q

lint:
	ruff check src tests

fmt:
	ruff format src tests

doctor:
	rambass doctor

status:
	rambass status

check:
	rambass check

clean:
	rm -rf reaper/build/*.rbs .pytest_cache .ruff_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
