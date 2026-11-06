.PHONY: test wall nightly serve init fmt
test:            ## unit + wall tests (CI gate)
	uv run pytest -q
wall:            ## wall tests only, including the slow GLiNER pass
	RADAR_TEST_GLINER=1 uv run pytest -q tests/wall
nightly:         ## live-store wall sweep + canary (run by launchd/cron)
	uv run radar wall
init:
	uv run radar init
serve:
	uv run radar serve --reload
