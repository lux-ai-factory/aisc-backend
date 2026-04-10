from typing import Callable


def metric(name: str):
    """
    Decorator to mark a method as a metric exporter.
    Methods decorated with this should return a list of Measure objects.
    """
    def decorator(func: Callable):
        func.metric_name = name
        return func
    return decorator