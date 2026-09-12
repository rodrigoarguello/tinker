"""Spotify: buscar con la app, escribir con el permiso de una persona.

`cliente` habla con la API; `cola` decide que entra a la playlist y cuando, con
reintentos que no tocan a nadie. Nada de esto corre dentro de un pedido del
juego: ver el comentario de cola.py.
"""
from app.spotify import cliente, cola

__all__ = ["cliente", "cola"]
