"""Read JSON HTTP data sources as DataFrames."""

import pandas as pd


def read_api(
    url: str,
    *,
    method: str = "GET",
    headers: dict | None = None,
    params: dict | None = None,
    json_body: dict | None = None,
    json_path: str | None = None,
    timeout: int = 30,
) -> pd.DataFrame:
    """Fetch data from a REST API and return as DataFrame.

    Args:
        url: API endpoint URL.
        method: HTTP method (GET or POST).
        headers: Request headers (e.g. Authorization).
        params: Query parameters.
        json_body: JSON body for POST requests.
        json_path: Dot-separated path to the array in the response JSON.
                   Example: "data.results" extracts response["data"]["results"].
        timeout: Request timeout in seconds.

    Returns:
        pandas DataFrame.
    """
    import requests as _requests

    response = _requests.request(
        method=method,
        url=url,
        headers=headers,
        params=params,
        json=json_body,
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()

    # Navigate to nested array if json_path is set
    if json_path:
        for key in json_path.split("."):
            data = data[key]

    return pd.json_normalize(data, sep="_") if isinstance(data, list) else pd.DataFrame([data])
