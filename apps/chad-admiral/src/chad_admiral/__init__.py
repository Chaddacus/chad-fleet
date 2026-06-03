"""chad-admiral — the reactive admiral tier of the chad-fleet HUB.

Exposed as an OpenAI-compatible service so the hub front door (chad-dashboard)
talks to it as a model. Runs discovery, freezes CaptainDossiers, and spawns captains as
auto_runtime tracks. See HUB_ARCHITECTURE.md § 2 (Admiral tier).
"""
