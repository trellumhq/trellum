"""Value-free errors for outbound API mail."""

_MESSAGES = {
    "invalid_config": "The email API configuration is invalid.",
    "invalid_message": "This email contains unsupported or unmapped content.",
    "request_too_large": "The email API request exceeds its size limit.",
    "network_policy": "The email API destination is not permitted.",
    "dns_error": "The email API destination could not be resolved safely.",
    "connection_error": "The email API connection failed.",
    "tls_error": "The email API TLS connection failed.",
    "timeout": "The email API request timed out; acceptance could not be confirmed.",
    "http_rejected": "The email API rejected the request.",
    "http_unconfirmed": "Email API acceptance could not be confirmed.",
    "response_invalid": "Email API acceptance could not be confirmed from the response.",
    "response_too_large": "Email API acceptance could not be confirmed from the response.",
}


class EmailDeliveryError(Exception):
    def __init__(self, category, *, outcome="not_submitted", status=None):
        if category not in _MESSAGES or outcome not in {
            "not_submitted", "rejected", "unconfirmed"
        }:
            raise ValueError("Invalid email error classification")
        self.category = category
        self.outcome = outcome
        self.status = status if type(status) is int and 100 <= status <= 599 else None
        super().__init__(_MESSAGES[category])
