"""Multi-supplier product pricing (Sprint 6).

One master product record (``products``) with per-supplier offers
(``supplier_products``). A simple fulfillment engine ranks suppliers by lowest
estimated TOTAL cost (materials + delivery + handling), never by unit price
alone. Supplier cost fields are restricted and never serialized to sales users.
"""
