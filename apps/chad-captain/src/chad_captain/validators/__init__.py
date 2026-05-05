"""Per-app custom validators.

Modules in this package implement ``validate_app_completion`` matching the
default chain's signature (see ``chad_captain.validator.validate_app_completion``)
and are referenced from ``RegisteredApp.validator_module``.

Custom validators OWN the entire chain: the captain does NOT post-process
their verdict with reuse_regression or verify_gate. They MUST be FAIL-CLOSED
on configuration / dependency / environment errors.
"""
