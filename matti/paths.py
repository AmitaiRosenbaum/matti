from pathlib import Path

def workspace():
    """Resolve user data independently of the installed package location."""
    return Path.cwd()
