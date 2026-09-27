"""Parsers for the formats a tender set arrives in.

Everything in this package runs inside the sandbox (`firebid.sandbox`), takes bytes, and
returns plain data. Nothing here touches the database or object storage: a parser reads a
file a stranger sent us, and it holds nothing worth stealing.
"""
