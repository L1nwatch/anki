# Developer Notes

- Use the local virtualenv at `feng_custom/.venv` when running the CLB9 listening tool so Whisper is available.
- When testing the Flask tool, launch it via `/tmp/run_listening.sh` or activate the venv before running `python add_listening_notes_for_clb9.py`.
- Keep in mind the web UI now expects audio to be loaded via the page; the `--audio` CLI flag is optional.
- Log output for the helper script is currently streamed to `/tmp/clb9_listening.log`.
