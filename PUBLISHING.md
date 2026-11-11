# Publishing

Nothing here is published yet.

1. Check everything, against real boxd too:

   ```sh
   uv sync
   uv run ruff check .
   uv run ruff format --check .
   uv run ty check
   uv run pytest
   BOXD_E2E=1 BOXD_API_KEY=... DIFY_CLI_PATH=/path/to/dify-plugin \
     uv run python scripts/live_smoke.py
   ```

   The live test runs the plugin through the Dify plugin runtime and calls
   every tool. It runs against production boxd and deletes the machines it
   creates.

2. Set the version in `manifest.yaml` (`version`) and `pyproject.toml`, and
   the date in `CHANGELOG.md`. The first release ships `0.1.0` as it stands.
   `meta.version` in `manifest.yaml` is the manifest format, not the plugin
   version: leave it.

3. Check that the README banner uses its full
   `raw.githubusercontent.com` URL. The README is part of the package, and
   the Dify Marketplace doesn't show relative images from outside it.

4. Build the package with the
   [Dify plugin CLI](https://github.com/langgenius/dify-plugin-daemon/releases)
   and look at what is in it:

   ```sh
   dify-plugin plugin package . -o boxd-0.1.0.difypkg
   unzip -l boxd-0.1.0.difypkg
   ```

   It holds `manifest.yaml`, `main.py`, `_client.py`, `provider/`, `tools/`,
   `_assets/icon.png`, `requirements.txt`, `README.md`, `PRIVACY.md` and
   `LICENSE`. Tests, scripts, examples and the banner stay out
   (`.difyignore`).

5. Run the validators of
   [dify-marketplace-toolkit](https://github.com/langgenius/dify-marketplace-toolkit)
   on the package, the way the `dify-plugins` checks run them.

6. Install the package in a self-hosted Dify (**Plugins > Install plugin >
   Local Package File**), authorize it with a real key, import the three
   workflows in `examples/` and run each one. Delete the machine that
   `persistent_workspace.yml` leaves hibernated.

7. Tag and create the GitHub release with the package attached:

   ```sh
   git tag v0.1.0 && git push --tags
   gh release create v0.1.0 boxd-0.1.0.difypkg --title v0.1.0 --notes-file CHANGELOG.md
   ```

8. Publish to the Dify Marketplace: open a pull request on
   [langgenius/dify-plugins](https://github.com/langgenius/dify-plugins) that
   adds the package as `boxd/boxd/boxd-0.1.0.difypkg`, with the repo's pull
   request template filled in. The merge publishes the plugin.
