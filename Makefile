.PHONY: setup test check run install-launchd

UV ?= uv

setup:
	$(UV) sync --dev

test:
	$(UV) run python -m unittest discover -s tests -v

check:
	bash -n scripts/daily_pipeline.sh scripts/pure_videos.sh scripts/install_launchd.sh
	$(UV) run python -m compileall -q scripts tests
	$(UV) run python -m unittest discover -s tests -v

run:
	bash scripts/daily_pipeline.sh

pure-videos:
	bash scripts/pure_videos.sh

install-launchd:
	bash scripts/install_launchd.sh
