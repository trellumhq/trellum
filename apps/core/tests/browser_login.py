"""Sign in through the real two-step login form in a Playwright page.

``/login`` asks for the email, ``/login/password`` for the password. Tests that
only need an authenticated session should reuse a session cookie instead; this
is for the tests that must sign in the way a person does.
"""


def reach_password_step(page, origin, email, next_path=None):
    """Open the login page, enter the email, and wait for the password step."""
    page.goto(origin + "/login" + (f"?next={next_path}" if next_path else ""))
    page.get_by_label("Email", exact=True).fill(email)
    page.get_by_role("button", name="Continue", exact=True).click()
    page.wait_for_url(lambda url: "/login/password" in url)


def sign_in(page, origin, email, password, next_path=None):
    """Complete both steps. The caller waits for the page it expects next."""
    reach_password_step(page, origin, email, next_path)
    page.get_by_label("Password", exact=True).fill(password)
    page.get_by_role("button", name="Log in", exact=True).click()
