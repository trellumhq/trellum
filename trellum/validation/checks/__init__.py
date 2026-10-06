"""One module per family of checks.

Split out of a single 2,012-line validation.py, where ten unrelated rule
families shared one namespace and `_check_columns` alone ran to 371 lines.
Each module here owns one rule family and nothing else.
"""
