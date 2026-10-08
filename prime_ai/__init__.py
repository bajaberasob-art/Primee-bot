"""Internal PRIME AI components.

The existing prime_ai_* modules remain the public integration boundaries for
Discord, the dashboard, and storage. Import leaf components here directly;
this package deliberately does not import the bot or open database connections.
"""
