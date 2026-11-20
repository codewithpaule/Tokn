"""Tokn adapters.

Implements the ports declared by ``tokn_core`` against real infrastructure:
PostgreSQL, Redis, upstream model providers, Paystack and Resend.

Nothing outside this package may talk to those systems directly.
"""
