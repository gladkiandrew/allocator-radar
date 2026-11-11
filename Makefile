.PHONY: test wall nightly serve init fmt export-static deploy-public export-member deploy-gated
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
export-member:   ## render the members build (contacts shown) to dist-member/; for the gated Worker only
	uv run radar export-static --audience member --out dist-member
deploy-gated:    ## members build -> wall check (member) -> deploy the gated Worker (wrangler.gated.jsonc; secrets set once)
	uv run radar export-static --audience member --out dist-member
	uv run radar wall-static dist-member --audience member
	npx wrangler deploy -c wrangler.gated.jsonc
