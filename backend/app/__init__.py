"""Knowledge application package; API construction is lazy for portable adapters."""
def __getattr__(name):
    if name == "app":
        from .bootstrap import app
        return app
    raise AttributeError(name)
