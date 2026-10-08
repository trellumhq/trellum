"""Report and analysis manifest trust boundaries."""


def validate_content_config(config: object) -> dict:
    """Validate the content discriminator before executing project code."""
    if not isinstance(config, dict):
        raise ValueError("report.yaml must contain a mapping")
    if config.get("kind", "report") not in ("report", "analysis"):
        raise ValueError("report.yaml kind must be 'report' or 'analysis'")
    if "initial_audience" in config and config["initial_audience"] not in ("studio", "private"):
        raise ValueError("report.yaml initial_audience must be 'studio' or 'private'")
    if config.get("kind") == "analysis":
        for key in ("data_sources", "schedule", "extra_cdn"):
            if key in config:
                raise ValueError(f"Analysis report.yaml cannot declare {key}")
        for key in ("author", "name", "description"):
            if key in config and not isinstance(config[key], str):
                raise ValueError(f"Analysis {key} must be a plain string")
    return config
