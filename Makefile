.PHONY: test wall nightly serve init fmt export-static deploy-public
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
export-static:   ## render the public read-only site to dist/
	uv run radar export-static --out dist
deploy-public:   ## export-static -> wall check on dist/ -> deploy to allocator.amagna.co (Cloudflare Workers)
	uv run radar export-static --out dist
	uv run radar wall-static dist
	npx wrangler deploy
