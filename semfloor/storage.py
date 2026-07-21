"""
Static files storage.

WhiteNoise's manifest storage fingerprints files for long-term caching in production.
We subclass it with manifest_strict = False so that BEFORE `collectstatic` has run
(local dev, and the test runner, which forces DEBUG=False) a missing manifest entry
falls back to the plain filename instead of raising. After `collectstatic` on deploy,
the manifest exists and hashed URLs are used normally.
"""
from whitenoise.storage import CompressedManifestStaticFilesStorage


class WhiteNoiseStaticStorage(CompressedManifestStaticFilesStorage):
    manifest_strict = False
