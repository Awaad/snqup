"""Uploads.

Owns the `uploads` table and the two-phase flow: request a signed URL, then
confirm once the client has uploaded. Other domains store a PATH and ask this
one to turn it into a URL.
"""
