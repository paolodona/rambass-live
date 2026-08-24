.PHONY: help install install-audio install-lyrics install-all test lint fmt doctor status check clean

# Which installer to call. Resolved rather than hard-coded, because a venv built
# by `uv venv` -- which is how this checkout's is built -- contains no pip, and
# `make install` there fails with "pip: command not found" for a machine that
# can install perfectly well. Same order as the hints the CLI prints.
INSTALL := $(shell command -v pip >/dev/null 2>&1 && echo pip \
	|| { command -v uv >/dev/null 2>&1 && echo 'uv pip'; } \
	|| echo 'python -m pip')

help:
	@echo "install        core dependencies only"
	@echo "install-audio  + librosa/scipy (analyze, drums transcribe)"
	@echo "install-lyrics + faster-whisper (lyrics transcribe)"
	@echo "install-all    + demucs/torch (stems) and dev tools"
	@echo "test           run the test suite"
	@echo "lint / fmt     ruff check / ruff format"
	@echo "doctor         check the toolchain"
	@echo "status         progress board across both albums"
	@echo "check          validate every song.yaml"
	@echo "clean          remove generated build scripts and caches"
	@echo ""
	@echo "installing with: $(INSTALL)"

install:
	$(INSTALL) install -e .

install-audio:
	$(INSTALL) install -e '.[audio]'

install-lyrics:
	$(INSTALL) install -e '.[lyrics]'

install-all:
	$(INSTALL) install -e '.[audio,lyrics,separate,dev]'

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
