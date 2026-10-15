"""Shared plumbing: logging, metrics, health, shutdown, Kafka, tracing.

Every service carries an identical copy of this package, so each Docker build
context (apps/<service>) is self-contained. Compare copies with:
    diff -r apps/rider-api/app/core apps/driver-api/app/core
"""
