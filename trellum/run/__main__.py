"""Entry point for: python -m trellum.run"""

from trellum.log_capture import host_registered_secrets, run_filtered

if host_registered_secrets():
    raise SystemExit(run_filtered())

from trellum.runner import main  # noqa: E402

main()
