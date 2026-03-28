"""
scrapers/

Each module in this package is responsible for one data source.
Every public scrape function has the signature:

    async def scrape() -> dict

It must:
  - Return a dict of structured results (keys documented per module).
  - Never raise unhandled exceptions — callers in main.py handle errors.
  - Log progress via rich.Console where appropriate.
"""
