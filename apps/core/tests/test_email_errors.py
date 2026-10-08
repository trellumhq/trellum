from apps.core.email_errors import EmailDeliveryError


def test_error_is_fixed_copy_with_classification_only():
    error = EmailDeliveryError("http_rejected", outcome="rejected", status=401)
    assert error.category == "http_rejected"
    assert error.outcome == "rejected" and error.status == 401
    assert "secret" not in str(error)
