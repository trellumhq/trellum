"""Studio-wide roll-ups over built report output.

Both pages read what reports have already published — the experiment
overview from each A/B report's ``ab_compare`` payload, the annotations
calendar from ``events.yaml`` — and neither computes anything the reports do
not already contain. A portfolio view of reports is part of reading them.
"""
