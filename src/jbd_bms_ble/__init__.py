from pydoover.docker import run_app

from .application import JbdBmsBleApplication


def main():
    """Run the application."""
    run_app(JbdBmsBleApplication())
